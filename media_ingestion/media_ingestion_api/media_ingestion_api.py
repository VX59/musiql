from fastapi import APIRouter, Depends
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import exists, delete
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass, asdict
import json
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

import os
from pydantic import BaseModel, HttpUrl
from collections import defaultdict
import hashlib

from utility import make_uri
from s3_service import S3, get_S3
from database.models import MusiqlRepository, UserLirbary, UserRequestFixes
from database.db import get_session
from authtoken_api import get_current_user

router = APIRouter()


class MusiqlPayload(BaseModel):
    url: HttpUrl


class FixUploaderPayload(BaseModel):
    context: Dict


unknown_uploader_corrections: Dict = None
unknown_uploader_corrections_key = "unknown_uploader_corrections.json"

unknown_uploads_to_correct = []


def get_unknown_uploader_corrections(s3_service: S3) -> Dict:
    global unknown_uploader_corrections
    if unknown_uploader_corrections is None:
        try:
            file_stream = s3_service.pull_obj_stream(unknown_uploader_corrections_key)
            unknown_uploader_corrections = json.load(file_stream)
        except (KeyError, json.JSONDecodeError):
            unknown_uploader_corrections = defaultdict(dict)

    return unknown_uploader_corrections


def commit_unknown_uploader_corrections(
    s3_service: S3,
):
    global unknown_uploader_corrections
    data_bytes = json.dumps(unknown_uploader_corrections).encode("utf-8")
    s3_service.put_object(data_bytes, unknown_uploader_corrections_key)


@dataclass
class DownloadedResourceContext:
    file_hash: str
    obj_key: str
    uri: str
    uploader: str
    correction: str
    title: str
    url: str
    job_id: Optional[str] = None

    @classmethod
    def create_from_context_dict(cls, context: Dict):
        return cls(**context)
    
    @classmethod
    def create_from_user_request_fix(cls, request:UserRequestFixes):
        return cls(
            file_hash=request.file_hash.hex(),
            obj_key=request.file_path,
            uri=request.record_id,
            uploader=request.artist,
            correction=request.artist,
            title=request.title,
            url=request.url,
            job_id=request.uri
        )


async def is_known_uploader(name: str, session_maker: sessionmaker) -> bool:
    stmt = select(exists().where(MusiqlRepository.artists.ilike(f"%{name}%")))

    async with session_maker() as session:
        result = await session.execute(stmt)
        return result.scalar()


async def download_resource(
    url: HttpUrl, session_maker: sessionmaker, s3_service: S3
) -> Optional[Tuple[List[DownloadedResourceContext], List[DownloadedResourceContext]]]:
    ext = "mp3"
    outdir = "music_dump"

    uri = make_uri()
    outtmpl = os.path.join(outdir, uri)

    ydl_checking_opts = {
        "extract_flat": "in_playlist",
        "skip_download": True,
    }

    with YoutubeDL(ydl_checking_opts) as ydl:
        try:
            info = ydl.extract_info(str(url), download=False)
        except DownloadError:
            print(f"media unavailable {url}")
            return None

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bestaudio/best",
        "writethumbnail": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": ext,
                "preferredquality": "192",
            },
            {
                "key": "EmbedThumbnail",
            },
        ],
        "noplaylist": True,
    }

    entries = []
    if info.get("_type") == "playlist":
        entries = info["entries"]
    else:
        entries = [info]

    discovered_artists = []

    unkown_uploader_context: List[DownloadedResourceContext] = []
    known_uploader_context: List[DownloadedResourceContext] = []

    unknown_uploader_corrections = get_unknown_uploader_corrections(s3_service)

    for entry in entries:
        url = (
            entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry['id']}"
        )
        uri = make_uri()
        outtmpl = os.path.join(outdir, uri)

        ydl_opts["outtmpl"] = outtmpl
        
        title = info.get("title")
        uploader = info.get("uploader")

        stmt = select(MusiqlRepository).where(
            MusiqlRepository.title.ilike(f"%{title}%"),
            MusiqlRepository.artists.ilike(f"%{uploader}%")
        )

        async with session_maker() as session:
            result = await session.execute(stmt)
            record = result.scalars().first()
            if record is not None:
                print(f"duplicate record ignored {uploader} - {title}")
                continue

        with YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(str(url), download=True)

                obj_path = f"{outtmpl}.{ext}"
                obj_key = f"musiql_dump/{uri}.{ext}"

                file_hash = await s3_service.upload_object_from_path(
                    obj_path, obj_key, session_maker
                )


                context = DownloadedResourceContext(
                    file_hash=file_hash.hex(),
                    obj_key=obj_key,
                    uri=uri,
                    uploader=uploader,
                    correction=uploader,
                    title=title,
                    url=str(url),
                )

                if not await is_known_uploader(uploader, session_maker):
                    if uploader not in discovered_artists:
                        print(f"unknown uploader {uploader}")
                        discovered_artists.append(uploader)

                        if correction := unknown_uploader_corrections.get(uploader):
                            context.correction = correction

                        unkown_uploader_context.append(context)
                        unknown_uploads_to_correct.append(uri)
                else:
                    known_uploader_context.append(context)

            except Exception as e:
                print(e)
                continue

    return known_uploader_context, unkown_uploader_context


@router.post("/media_ingestion/try/receive", response_model=None)
async def receive_music(
    payload: MusiqlPayload,
    session_maker: sessionmaker = Depends(get_session),
    s3_service: S3 = Depends(get_S3),
    user_id: str = Depends(get_current_user)
):
    result = await download_resource(payload.url, session_maker, s3_service)
    if (result := await download_resource(payload.url, session_maker, s3_service)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"failed to fetch media @ {payload.url}"
        )

    known_uploader_context: List[DownloadedResourceContext] = result[0]
    unknown_uploader_context: List[DownloadedResourceContext] = result[1]
    uploader_display_name = user_id.split("-")[0]

    resources = []
    library_records = []

    for context in known_uploader_context:
        file_hash = bytes.fromhex(context.file_hash)

        new_resource = MusiqlRepository(
            uri=context.uri,
            title=context.title,
            artists=context.uploader,
            filepath=context.obj_key,
            hash=file_hash,
            mime="audio/mpeg",
            metadata_json={},
            url=str(payload.url),
            added_by=uploader_display_name
        )

        resources.append(new_resource)

        new_library_record = UserLirbary(
            user_id=user_id,
            record_id=context.uri
        )

        library_records.append(new_library_record)

    async with session_maker() as session, session.begin():
        session.add_all(resources)
        session.add_all(library_records)

    headers = {"Content-Type": "application/json"}

    if unknown_uploader_context:

        requests = []

        for context in unknown_uploader_context:
            file_hash = bytes.fromhex(context.file_hash)
            job_id = f"job-{make_uri()}"
            new_request_fix = UserRequestFixes(
                user_id=user_id,
                title=context.title,
                artist=context.uploader,
                record_id=context.uri,
                file_hash=file_hash,
                url=context.url,
                file_path=context.obj_key,
                uri=job_id
            )

            requests.append(new_request_fix)

        async with session_maker() as session, session.begin():
            session.add_all(requests)

        return JSONResponse(content={"needs_fix": True}, headers=headers)
    else:
        return JSONResponse(content={"needs_fix": False}, headers=headers)


@router.get("/media_ingestion/get_fixes")
async def get_request_fixes(
    session_maker:sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user)
):
    stmt = select(UserRequestFixes).where(UserRequestFixes.user_id == user_id)

    async with session_maker() as session:
        result = await session.execute(stmt)
        requests:List[UserRequestFixes] = result.scalars().all()
        headers = {"Content-Type": "application/json"}

        if not requests:
            return JSONResponse(
                content=[],
                headers=headers
            )

        fixes_context = [
            DownloadedResourceContext.create_from_user_request_fix(request)
            for request
            in requests
        ]

        serialized_requests = json.loads(
            json.dumps([asdict(ctx) for ctx in fixes_context])
        )

        return JSONResponse(
            content=serialized_requests,
            headers=headers
        )


@router.post("/media_ingestion/fix_uploader", response_model=None)
async def fix_uploader(
    payload: FixUploaderPayload,
    session_maker: sessionmaker = Depends(get_session),
    s3_service: S3 = Depends(get_S3),
    user_id: str = Depends(get_current_user)
):

    file_hash = bytes.fromhex(payload.context.get("file_hash"))
    context = DownloadedResourceContext.create_from_context_dict(payload.context)
    uploader_display_name = user_id.split("-")[0]

    new_resource = MusiqlRepository(
        uri=context.uri,
        title=context.title,
        artists=context.correction,
        filepath=context.obj_key,
        hash=file_hash,
        mime="audio/mpeg",
        metadata_json={},
        url=context.url,
        added_by=uploader_display_name
    )

    new_library_record = UserLirbary(
        user_id=user_id,
        record_id=context.uri
    )

    stmt = delete(UserRequestFixes).where(UserRequestFixes.uri == context.job_id)


    async with session_maker() as session, session.begin():
        session.add(new_resource)
        session.add(new_library_record)
        await session.execute(stmt)

    unknown_uploader_corrections = get_unknown_uploader_corrections(s3_service)

    uuc_hash = hashlib.sha256(
        json.dumps(unknown_uploader_corrections).encode("utf-8")
    ).hexdigest()

    if context.uploader != context.correction:
        unknown_uploader_corrections[context.uploader] = context.correction

    new_uuc_hash = hashlib.sha256(
        json.dumps(unknown_uploader_corrections).encode("utf-8")
    ).hexdigest()

    unknown_uploads_to_correct.remove(context.uri)

    # only upload if all corrections were made/confirmed AND the mapping actually changed
    if not unknown_uploads_to_correct and uuc_hash != new_uuc_hash:
        commit_unknown_uploader_corrections(s3_service)

    return JSONResponse(
        content={"status": f"successfully fixed {context.uri}, {context.uploader}"}
    )
