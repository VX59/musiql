from pydantic import BaseModel, HttpUrl
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from db import async_session
from models import MusiqlRepository, MusiqlHistory
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
import os
from sqlalchemy import func, update, exists
from sqlalchemy.future import select
import hashlib
import secrets
import os
from aioconsole import ainput
from datetime import datetime, timezone

router = APIRouter()

class MusiqlPayload(BaseModel):
    url: HttpUrl

class SearchPayload(BaseModel):
    history_id: int
    search_term: str
    duration_played: float

class SkipPayload(BaseModel):
    history_id: int
    duration_played: float

async def verify_artist_name(name: str) -> bool:
    stmt = select(exists().where(MusiqlRepository.artists.ilike(f"%{name}%")))

    async with async_session() as session:
        result = await session.execute(stmt)
        return result.scalar()

async def download_resource(url:HttpUrl) -> tuple[str, str, str]:
    
    ext = "mp3"
    outdir = "../musiql/music_dump"
    upload_data:list[tuple[str,str, dict]] = []

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
            return upload_data

    ydl_opts = {
        'outtmpl': outtmpl,
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': ext,
            'preferredquality': '192',
        }],
        'noplaylist' : True
    }

    entries = []
    if info.get('_type') == 'playlist':
        entries = info['entries']
    else:
        entries = [info]

    discovered_artists_cache = []
    for entry in entries:
        url = entry.get('webpage_url') or f"https://www.youtube.com/watch?v={entry['id']}"
        uri = make_filename()
        outtmpl = os.path.join(outdir, uri)

        ydl_opts['outtmpl'] = outtmpl
        with YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(str(url))
                uploader = info.get("uploader")
                if not await verify_artist_name(uploader):
                    if uploader not in discovered_artists_cache:
                        print(discovered_artists_cache)
                        answer = await ainput(f"New artist found [{uploader}], confirm name? ")
                        answer = answer.strip()
                        if answer.strip():
                            info["uploader"] = answer.strip()
                            if answer not in discovered_artists_cache:
                                discovered_artists_cache.append(answer)
                        else:
                            if uploader not in discovered_artists_cache:                            
                                discovered_artists_cache.append(uploader)

                filepath = os.path.join(outdir, f"{uri}.{ext}")
                upload_data.append((filepath, uri, info))

            except DownloadError as e:
                continue

    return upload_data

async def resource_exists(hash: bytes):
    stmt = select(MusiqlRepository).where(MusiqlRepository.hash == hash)

    async with async_session() as session:

        result = await session.execute(stmt)
        record = result.scalars().first()
        if record is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="attempting to upload duplicate record")

@router.get("/musiql/serve/{uri}")
async def serve_record(uri: str):

    stmt = select(MusiqlRepository).where(MusiqlRepository.uri == uri)
    async with async_session() as session:
        result = await session.execute(stmt)
        record = result.scalars().first()
        
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
        
        filename = record.filepath.split("/")[-1]
        history_id = await track_history(record.uri, session)

        return FileResponse(path=record.filepath, media_type=record.mime, filename=filename,  headers={"Cache-Control": "no-store", "X-history-id": str(history_id)})

@router.post("/musiql/", response_model=None)
async def receive_music(payload: MusiqlPayload):

    for path, uri, info in await download_resource(payload.url):
        with open(path, "rb") as reader:
            hash = hashlib.sha256(reader.read()).digest()
            reader.close()
        try:
            await resource_exists(hash)
        except HTTPException as e:
            os.remove(path)
            return {"status" : e.detail}

        new_resource = MusiqlRepository(
            uri=uri,
            title=info.get('title'),
            artists = info.get('uploader'),
            filepath=path,
            hash=hash,
            mime="audio/mpeg",
            metadata_json={"ext":path.split(".")[-1]},
        )
        async with async_session() as session, session.begin():
            session.add(new_resource)

    return {"status": "ok"}

async def select_song(search_term):
    
    stmt = select(MusiqlRepository).where(MusiqlRepository.title.ilike(f"%{search_term}%"))

    async with async_session() as session:

        result = await session.execute(stmt)
        record = result.scalars().first()
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")

    return record

@router.post("/musiql/search/", response_model=None)
async def search_song(payload: SearchPayload = None):

    if payload.history_id > 0:
        await update_duration(payload.history_id,payload.duration_played)

    record = await select_song(payload.search_term)
    response={
        "uri": record.uri,
        "title": record.title,
        "artists": record.artists
    }

    return response

@router.get("/musiql/player/")
async def serve_player():
    html_path = "./index.html"
    return FileResponse(path=html_path, media_type="text/html")

@router.post("/musiql/log/engagement/")
async def log_engagement(skip_payload: SkipPayload):
    await update_duration(skip_payload.history_id, skip_payload.duration_played)
    return {"status" : "ok"}

@router.get("/musiql/sample/")
async def sample_song():
    stmt = select(MusiqlRepository).order_by(func.random()).limit(1)

    async with async_session() as session:
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

async def update_duration(history_id: int, duration: float):
    stmt = update(MusiqlHistory).values(duration_played=duration).where(MusiqlHistory.id == history_id)
    async with async_session() as session:
        await session.execute(stmt)
        await session.commit()