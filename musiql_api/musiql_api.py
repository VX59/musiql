from pydantic import BaseModel, HttpUrl
from fastapi import APIRouter, HTTPException, status, Depends
from musiql_api.settings import Settings, get_settings
from musiql_api.db import get_session
from musiql_api.s3_service import S3Service, DuplicateResource
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from musiql_api.models import MusiqlRepository, MusiqlHistory
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from sqlalchemy import update, exists, or_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
import secrets
import os
from datetime import datetime, timezone
from .GraphAMP import GraphAMP
from typing import Optional, List, Tuple, Dict
from dataclasses import dataclass, asdict
import json

router = APIRouter()


class MusiqlPayload(BaseModel):
    url: HttpUrl


class AdvancedSearchPayload(BaseModel):
    history_id: int
    search_term: str
    duration_played: float


class SkipPayload(BaseModel):
    history_id: int
    duration_played: float


class FixUploaderPayload(BaseModel):
    context:dict


@router.get("/musiql/serve/{uri}")
async def serve_record(
    uri: str,
    session_maker:sessionmaker = Depends(get_session),
    s3_service:S3Service = Depends(S3Service.get_s3_service)
):

    stmt = select(MusiqlRepository).where(MusiqlRepository.uri == uri)
    async with session_maker() as session:
        result = await session.execute(stmt)
        record = result.scalars().first()
        
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
        
        history_id = await track_history(record.uri, session)
        filename = f"{record.uri}.mp3"
        s3_key = f"musiql_dump/{filename}"

        try:
            url = s3_service.get_presigned_url(s3_key)
            body = {"url": url}
            headers = {
                "Content-Type": "application/json",
                "X-history-id": str(history_id)
            } 
            return JSONResponse(
                content=body,
                headers=headers
            )

        except Exception as e:
            print(f"Encountered error during s3 fetch {e} this fallback should only occur in local development")
            headers = {
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-store",
                "X-history-id": str(history_id)
            }

            return FileResponse(
                path=record.filepath,
                media_type=record.mime,
                filename=filename,
                headers=headers
            )


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
                info = ydl.extract_info(str(url), download=False)

                stream_url = info.get("url")
                headers = info.get("http_headers", {})
                obj_key = f"musiql_dump/{uri}.{ext}"

                file_hash = await s3_service.upload_object(
                    stream_url,
                    obj_key,
                    headers,
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


@router.post("/musiql/try/receive", response_model=None)
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


@router.post("/musiql/fix_uploader", response_model=None)
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


async def select_song(search_term, session_maker:sessionmaker = Depends(get_session)):
    stmt = select(MusiqlRepository).where(MusiqlRepository.title.ilike(f"%{search_term}%"))
    async with session_maker() as session:

        result = await session.execute(stmt)
        record = result.scalars().first()
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")

    return record


@router.post("/musiql/search/advanced", response_model=None)
async def advanced_search_songs(
    payload: AdvancedSearchPayload = None,
    session_maker:sessionmaker = Depends(get_session)
):
    stmt = (
        select(MusiqlRepository)
        .where(
            or_(
                MusiqlRepository.title.ilike(f"%{payload.search_term}%"),
                MusiqlRepository.artists.ilike(f"%{payload.search_term}%")
            )
        )
    )

    async with session_maker() as session:
        result = await session.execute(stmt)
        records = result.scalars().all()

        if records is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no records found")


    if len(records) == 1 and payload.history_id > 0:
        await update_duration(
            payload.history_id,
            payload.duration_played,
            session_maker=session_maker
        )

    response={
        "num_results": len(records),
        "results":[{"uri":r.uri, "title":r.title, "artists":r.artists} for r in records]
    }

    return response


@router.get("/musiql/player/", response_class=HTMLResponse)
async def serve_player(settings: Settings = Depends(get_settings)):
    html_path = "./musiql-desktop/index.html"

    # Read the HTML and inject the API URL
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    html_content = html_content.replace("{{API_URL}}", settings.api_url)

    return HTMLResponse(content=html_content, media_type="text/html")


@router.post("/musiql/log/engagement/")
async def log_engagement(
    skip_payload: SkipPayload,
    session_maker:sessionmaker = Depends(get_session)
):
    await update_duration(
        skip_payload.history_id,
        skip_payload.duration_played,
        session_maker=session_maker
    )
    return {"status" : "ok"}


@router.get("/musiql/sample/{uri}")
async def sample_song(
    uri:Optional[str],
    session_maker:sessionmaker = Depends(get_session),
    recommendation_model = Depends(GraphAMP.get_model)
):

    state = recommendation_model.sample(uri)

    stmt = select(MusiqlRepository).where(MusiqlRepository.uri == state)

    async with session_maker() as session:
        result = await session.execute(stmt)
        sample_record = result.scalars().first()
        if not sample_record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no record found")

        response={
            "uri": sample_record.uri,
            "title": sample_record.title,
            "artists": sample_record.artists
        }

        return response


async def track_history(uri: str, session):
    new_record = MusiqlHistory(
        uri= uri,
        duration_played= 1.0,
        listened_at = datetime.now(timezone.utc)
    )
    
    session.add(new_record)
    await session.commit()

    return new_record.id


async def update_duration(
        history_id: int,
        duration: float,
        session_maker:sessionmaker
    ):
    
    stmt = update(MusiqlHistory).values(duration_played=duration).where(MusiqlHistory.id == history_id)
    async with session_maker() as session:
        await session.execute(stmt)
        await session.commit()