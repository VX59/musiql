from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status, Depends
from settings import Settings, get_settings
from database.db import get_session
from s3_service import S3Service
from fastapi.responses import HTMLResponse, JSONResponse
from database.models import MusiqlRepository, MusiqlHistory
from sqlalchemy import update, or_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from datetime import datetime, timezone
from .GraphAMP import GraphAMP
from typing import Optional

router = APIRouter()


class AdvancedSearchPayload(BaseModel):
    history_id: int
    search_term: str
    duration_played: float


class SkipPayload(BaseModel):
    history_id: int
    duration_played: float


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

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    html_content = html_content.replace("{{MUSIQL_API_URL}}", settings.musiql_api_url)
    html_content = html_content.replace("{{MEDIA_INGESTION_API_URL}}", settings.media_ingestion_api_url)

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
    recommendation_api:GraphAMP = Depends(GraphAMP.get_recommendation_api)
):
    state = recommendation_api.sample(uri)
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