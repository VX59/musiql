from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import exists
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass, asdict
import json
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
import secrets
import os
from pydantic import BaseModel, HttpUrl

from s3_service import S3Service
from database.models import MusiqlRepository
from database.db import get_session

router = APIRouter()


class MusiqlPayload(BaseModel):
    url: HttpUrl


class FixUploaderPayload(BaseModel):
    context:dict


async def is_known_uploader(name: str, session_maker:sessionmaker) -> bool:
    stmt = select(exists().where(MusiqlRepository.artists.ilike(f"%{name}%")))

    async with session_maker() as session:
        result = await session.execute(stmt)
        return result.scalar()


@dataclass       
class DownloadedResourceContext:
    file_hash: str
    obj_key: str
    uri: str
    uploader: str
    title: str
    url: str

    @classmethod
    def create_from_context_dict(cls, context:Dict):
        return cls(**context)


async def download_resource(
    url:HttpUrl,
    session_maker:sessionmaker,
    s3_service:S3Service
) -> Optional[Tuple[
    List[DownloadedResourceContext],
    List[DownloadedResourceContext]
    ]
]:
    
    ext = "mp3"
    outdir = "music_dump"

    def make_filename():
        uri = f"{secrets.randbelow(0x1000000):06x}"
        return uri
    
    uri = make_filename()
    outtmpl = os.path.join(outdir, uri)

    ydl_checking_opts = {
        'extract_flat': 'in_playlist',
        'skip_download': True,
    }

    with YoutubeDL(ydl_checking_opts) as ydl: 
        try:
            info = ydl.extract_info(str(url), download=False)
        except DownloadError as e:
            print(f"media unavailable {url}")
            return None

    ydl_opts = {
        'outtmpl': outtmpl,
        'format': 'bestaudio/best',
        'writethumbnail': True,
        'postprocessors': [
            {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': ext,
            'preferredquality': '192',
            },
            {
                'key': 'EmbedThumbnail',
            }
        ],
        'noplaylist' : True
    }

    entries = []
    if info.get('_type') == 'playlist':
        entries = info['entries']
    else:
        entries = [info]

    discovered_artists = []

    unkown_uploader_context:List[DownloadedResourceContext] = []
    known_uploader_context:List[DownloadedResourceContext] = []

    for entry in entries:
        url = entry.get('webpage_url') or f"https://www.youtube.com/watch?v={entry['id']}"
        uri = make_filename()
        outtmpl = os.path.join(outdir, uri)

        ydl_opts['outtmpl'] = outtmpl

        with YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(str(url), download=True)

                obj_path = f"{outtmpl}.{ext}"
                obj_key = f"musiql_dump/{uri}.{ext}"

                file_hash = await s3_service.upload_object(
                    obj_path,
                    obj_key,
                    session_maker
                )

                uploader = info.get("uploader")

                context = DownloadedResourceContext(
                    file_hash=file_hash.hex(),
                    obj_key=obj_key,
                    uri=uri,
                    uploader=uploader,
                    title=info.get("title"),
                    url=str(url)
                )

                if not await is_known_uploader(uploader, session_maker):
                    if uploader not in discovered_artists:
                        print(f"unknown uploader {uploader}")
                        discovered_artists.append(uploader)
                        unkown_uploader_context.append(context)
                else:
                    known_uploader_context.append(context)

            except Exception as e:
                print(e)
                continue

    return known_uploader_context, unkown_uploader_context 


@router.post("/media_ingestion/try/receive", response_model=None)
async def receive_music(
    payload: MusiqlPayload,
    session_maker:sessionmaker = Depends(get_session),
    s3_service:S3Service = Depends(S3Service.get_s3_service)    
):
    result = await download_resource(payload.url, session_maker, s3_service)
    known_uploader_context:List[DownloadedResourceContext] = result[0]
    unknown_uploader_context:List[DownloadedResourceContext] = result[1]

    for context in known_uploader_context:

        new_resource = MusiqlRepository(
            uri=context.uri,
            title=context.title,
            artists=context.uploader,
            filepath=context.obj_key,
            hash=context.file_hash,
            mime="audio/mpeg",
            metadata_json={},
            url=str(payload.url)
        )
        async with session_maker() as session, session.begin():
            session.add(new_resource)

    headers = { "Content-Type": "application/json" } 
    
    if unknown_uploader_context:

        print(unknown_uploader_context)

        serialized_unknown_uploader_context = json.loads(
            json.dumps(
                [asdict(ctx) for ctx in unknown_uploader_context]
            )
        )

        return JSONResponse(
            content={
                "needs_fix": True,
                "unknown_uploaders": serialized_unknown_uploader_context 
            },
            headers=headers,
        )
    else:
        return JSONResponse(
            content={
                "needs_fix": False
            },
            headers=headers
        )


@router.post("/media_ingestion/fix_uploader", response_model=None)
async def fix_uploader(
    payload: FixUploaderPayload,
    session_maker:sessionmaker = Depends(get_session),
):

    file_hash = bytes.fromhex(payload.context.get("file_hash"))

    context = DownloadedResourceContext.create_from_context_dict(payload.context)

    new_resource = MusiqlRepository(
        uri=context.uri,
        title=context.title,
        artists=context.uploader,
        filepath=context.obj_key,
        hash=file_hash,
        mime="audio/mpeg",
        metadata_json={},
        url=context.url
    )
    async with session_maker() as session, session.begin():
        session.add(new_resource)
    
    return JSONResponse(
        content={"status": f"successfully fixed {context.uri}, {context.uploader}"}
    )

