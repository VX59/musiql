from pydantic import BaseModel, HttpUrl
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from db import async_session
from models import MusiqlRepository, MusiqlHistory
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
import os
from sqlalchemy import text, func, update
from sqlalchemy.future import select
import hashlib
import secrets
import os
from datetime import datetime, timezone

router = APIRouter()

class MusiqlPayload(BaseModel):
    url: HttpUrl

class SearchPayload(BaseModel):
    search_term: str

class SkipPayload(BaseModel):
    history_id: int
    duration_played: float

def download_resource(url:HttpUrl) -> tuple[str, str, str]:
    
    ext = "mp3"
    outdir = "../musiql/music_dump"
    upload_data:list[tuple[str,str, dict]] = []

    def make_filename():
        uri = f"{secrets.randbelow(0x1000000):06x}"
        return uri
    
    uri = make_filename()
    outtmpl = os.path.join(outdir, uri)

    ydl_opts = {
        'outtmpl': outtmpl,
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': ext,
            'preferredquality': '192',
        }],
        'noplaylist' : False
    }

    with YoutubeDL(ydl_opts) as ydl: 
        try:
            info = ydl.extract_info(str(url), download=False)
        except DownloadError as e:
            print(f"media unavailable {url}")
            return upload_data


    if info.get('_type') == 'playlist':
        for entry in info['entries']:
            url = entry['webpage_url']
            uri = make_filename()
            outtmpl = os.path.join(outdir, uri)

            ydl_opts['outtmpl'] = outtmpl

            with YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(str(url))
                except DownloadError as e:
                    continue

            filepath = os.path.join(outdir, f"{uri}.{ext}")
            upload_data.append((filepath, uri, entry))
    else:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(str(url))

        filepath = os.path.join(outdir, f"{uri}.{ext}")
        upload_data.append((filepath, uri, info))

    return upload_data

async def resource_exists(hash: bytes):
    stmt = select(MusiqlRepository).where(MusiqlRepository.hash == hash)

    async with async_session() as session:

        result = await session.execute(stmt)
        record = result.scalars().first()
        if record is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="attempting to upload duplicate record")

@router.post("/musiql/", response_model=None)
async def receive_music(payload: MusiqlPayload):

    for path, uri, info in download_resource(payload.url):
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

        history_id = await track_history(record.uri, session)
    return record, history_id

@router.post("/musiql/search/", response_model=None)
async def search_song(payload: SearchPayload = None, search_term: str = None):

    term = payload.search_term if not payload is None else search_term

    record, history_id = await select_song(term)

    filename = record.filepath.split("/")[-1]
    return FileResponse(path=record.filepath, media_type=record.mime, filename=filename,  headers={"Cache-Control": "no-store", "X-History-ID": str(history_id)})

@router.get("/musiql/player/")
async def serve_player():
    html_path = "./index.html"
    return FileResponse(path=html_path, media_type="text/html")

@router.post("/musiql/skip/")
async def skip_song(skip_payload: SkipPayload):
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
        
        history_id = await track_history(sample_record.uri, session)

        return FileResponse(path=sample_record.filepath, media_type=sample_record.mime, filename=sample_record.filepath.split("/")[-1], headers={"Cache-Control": "no-store", "X-History-ID": str(history_id)})
    
async def track_history(uri: str, session):
    new_record = MusiqlHistory(
        uri= uri,
        duration_played= 1.0, # update later when player presses a skip button,
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