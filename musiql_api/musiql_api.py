from pydantic import BaseModel, HttpUrl
from fastapi import APIRouter, HTTPException, status, Depends
from musiql_api.settings import Settings, get_settings
from musiql_api.db import get_session
from fastapi.responses import HTMLResponse, FileResponse
from musiql_api.models import MusiqlRepository, MusiqlHistory
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
import os
from sqlalchemy import update, exists, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import hashlib
import secrets
import os
from aioconsole import ainput
from datetime import datetime, timezone
from .GraphAMP import GraphAMP

router = APIRouter()

recommendation_model = GraphAMP()

class MusiqlPayload(BaseModel):
    url: HttpUrl

class AdvancedSearchPayload(BaseModel):
    history_id: int
    search_term: str
    duration_played: float

class SkipPayload(BaseModel):
    history_id: int
    duration_played: float

async def verify_artist_name(name: str, async_session:AsyncSession) -> bool:
    stmt = select(exists().where(MusiqlRepository.artists.ilike(f"%{name}%")))

    async with async_session() as session:
        result = await session.execute(stmt)
        return result.scalar()

async def download_resource(url:HttpUrl, async_session:AsyncSession) -> tuple[str, str, str]:
    
    ext = "mp3"
    outdir = "music_dump"
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
                if not await verify_artist_name(uploader, async_session):
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

async def resource_exists(hash: bytes, async_session:AsyncSession):
    stmt = select(MusiqlRepository).where(MusiqlRepository.hash == hash)

    async with async_session() as session:

        result = await session.execute(stmt)
        record = result.scalars().first()
        if record is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="attempting to upload duplicate record")

@router.get("/musiql/serve/{uri}")
async def serve_record(uri: str, async_session:AsyncSession = Depends(get_session)):

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
async def receive_music(payload: MusiqlPayload, async_session:AsyncSession = Depends(get_session)):

    for path, uri, info in await download_resource(payload.url, async_session):
        with open(path, "rb") as reader:
            hash = hashlib.sha256(reader.read()).digest()
            reader.close()
        try:
            await resource_exists(hash, async_session)
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
            url=str(payload.url)
        )
        async with async_session() as session, session.begin():
            session.add(new_resource)

    return {"status": "ok"}

async def select_song(search_term, async_session:AsyncSession = Depends(get_session)):
    
    stmt = select(MusiqlRepository).where(MusiqlRepository.title.ilike(f"%{search_term}%"))

    async with async_session() as session:

        result = await session.execute(stmt)
        record = result.scalars().first()
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")

    return record

@router.post("/musiql/search/advanced", response_model=None)
async def advanced_search_songs(payload: AdvancedSearchPayload = None, async_session:AsyncSession = Depends(get_session)):
    stmt = (
        select(MusiqlRepository)
        .where(
            or_(
                MusiqlRepository.title.ilike(f"%{payload.search_term}%"),
                MusiqlRepository.artists.ilike(f"%{payload.search_term}%")
            )
        )
    )

    async with async_session() as session:
        result = await session.execute(stmt)
        records = result.scalars().all()

        if records is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no records found")


    if len(records) == 1 and payload.history_id > 0:
        await update_duration(
            payload.history_id,
            payload.duration_played,
            async_session=async_session
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
async def log_engagement(skip_payload: SkipPayload, async_session:AsyncSession = Depends(get_session)):
    await update_duration(
        skip_payload.history_id,
        skip_payload.duration_played,
        async_session=async_session
    )
    return {"status" : "ok"}

@router.get("/musiql/sample/")
async def sample_song(async_session:AsyncSession = Depends(get_session)):

    await recommendation_model.sample()
    stmt = select(MusiqlRepository).where(MusiqlRepository.uri == recommendation_model.model_state)

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

async def update_duration(
        history_id: int,
        duration: float,
        async_session:AsyncSession
    ):
    
    stmt = update(MusiqlHistory).values(duration_played=duration).where(MusiqlHistory.id == history_id)
    async with async_session() as session:
        await session.execute(stmt)
        await session.commit()