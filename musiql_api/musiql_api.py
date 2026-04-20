from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status, Depends
from settings import Settings, get_settings
from database.db import get_session
from s3_service import S3Service, get_s3_service
from fastapi.responses import HTMLResponse, JSONResponse
from database.models import MusiqlRepository, MusiqlHistory, Users, UserLirbary
from sqlalchemy import update, or_, Select, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from datetime import datetime, timezone
from .models_api import GraphAMP, get_recommendation_api
from typing import List, Optional
from enum import Enum

from authtoken_api import get_current_user

musiql_api_router = APIRouter()


class AdvancedSearchPayload(BaseModel):
    history_id: int
    search_term: str
    duration_played: float


class SkipPayload(BaseModel):
    history_id: int
    duration_played: float


async def track_history(uri: str, user_id: str, session):
    new_record = MusiqlHistory(
        uri=uri, user_id=user_id, duration_played=1.0, listened_at=datetime.now(timezone.utc)
    )

    session.add(new_record)
    await session.commit()

    return new_record.id


@musiql_api_router.get("/musiql/serve/{uri}")
async def serve_record(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    s3_service: S3Service = Depends(get_s3_service),
    user_id:str = Depends(get_current_user)
):
    stmt = select(MusiqlRepository).where(MusiqlRepository.uri == uri)
    async with session_maker() as session:
        result = await session.execute(stmt)
        record = result.scalars().first()

        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="record not found"
            )

        history_id = await track_history(record.uri, user_id, session)
        filename = f"{record.uri}.mp3"
        s3_key = f"musiql_dump/{filename}"

        url = s3_service.get_presigned_url(s3_key)
        body = {"url": url}
        headers = {"Content-Type": "application/json", "X-history-id": str(history_id)}
        return JSONResponse(content=body, headers=headers)


async def select_song(search_term, session_maker: sessionmaker = Depends(get_session)):
    stmt = select(MusiqlRepository).where(
        MusiqlRepository.title.ilike(f"%{search_term}%")
    )
    async with session_maker() as session:
        result = await session.execute(stmt)
        record = result.scalars().first()
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="record not found"
            )

    return record


class QueryLang(str, Enum):
    library = "library"


def parse_search_query(search_term:str, user_id) -> Optional[Select]:
    commands = [term[1:] for term in search_term.split(" ") if term[0] == '@']
    for command in commands:
        match command:
            case QueryLang.library:
                stmt = (
                    select(MusiqlRepository)
                    .select_from(UserLirbary)
                    .join(MusiqlRepository, UserLirbary.record_id == MusiqlRepository.uri)
                    .where(UserLirbary.user_id == user_id)
                ).order_by(MusiqlRepository.created.desc())

                return stmt, QueryLang.library


@musiql_api_router.post("/musiql/search/advanced", response_model=None)
async def advanced_search_songs(
    payload: AdvancedSearchPayload = None,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user)
):
    
    if (result := parse_search_query(payload.search_term, user_id)) is not None:
        stmt, command = result
    else:
        command = None
        stmt = select(MusiqlRepository).where(
            or_(
                MusiqlRepository.title.ilike(f"%{payload.search_term}%"),
                MusiqlRepository.artists.ilike(f"%{payload.search_term}%"),
                MusiqlRepository.added_by.ilike(f"%{payload.search_term}%"),
            )
        ).order_by(MusiqlRepository.created.desc())

    async with session_maker() as session:
        result = await session.execute(stmt)
        records = result.scalars().all()

        if records is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="no records found"
            )

    if command != QueryLang.library:
        identity_stmt = (
            select(MusiqlRepository)
            .select_from(UserLirbary)
            .join(MusiqlRepository, UserLirbary.record_id == MusiqlRepository.uri)
            .where(
                UserLirbary.user_id == user_id,
                or_(
                    MusiqlRepository.title.ilike(f"%{payload.search_term}%"),
                    MusiqlRepository.artists.ilike(f"%{payload.search_term}%"),
                    MusiqlRepository.added_by.ilike(f"%{payload.search_term}%"),
                )
            ).order_by(MusiqlRepository.created.desc())
        )

        async with session_maker() as session:
            result = await session.execute(identity_stmt)
            identity_records = result.scalars().all()

            identity_uris = [r.uri for r in identity_records]
            
            in_identity = [
                True if uri in identity_uris else False
                for uri in
                [r.uri for r in records]
            ]

        search_context = list(zip(records, in_identity))                    

        response = {
            "num_results": len(records),
            "results": [
                {"uri": r.uri, "title": r.title, "artists": r.artists, "added_by": r.added_by, "in_library": in_identity} for r, in_identity in search_context
            ],
        }
    else:
        response = {
            "num_results": len(records),
            "results": [
                {"uri": r.uri, "title": r.title, "artists": r.artists, "added_by": r.added_by, "in_library": True} for r in records
            ],
        }

    if len(records) == 1 and payload.history_id > 0:
        await update_duration(
            payload.history_id, payload.duration_played, session_maker=session_maker
        )

    return response


@musiql_api_router.get("/musiql/player/", response_class=HTMLResponse)
async def serve_player(
    settings: Settings = Depends(get_settings),
):
    html_path = "./musiql-desktop/index.html"

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    html_content = html_content.replace("{{MUSIQL_API_URL}}", settings.musiql_api_url)
    html_content = html_content.replace(
        "{{MEDIA_INGESTION_API_URL}}", settings.media_ingestion_api_url
    )

    return HTMLResponse(content=html_content, media_type="text/html")


async def update_duration(
    history_id: int, duration: float, session_maker: sessionmaker
):
    stmt = (
        update(MusiqlHistory)
        .values(duration_played=duration)
        .where(MusiqlHistory.id == history_id)
    )
    async with session_maker() as session:
        await session.execute(stmt)
        await session.commit()


@musiql_api_router.post("/musiql/log/engagement/")
async def log_engagement(
    skip_payload: SkipPayload, session_maker: sessionmaker = Depends(get_session), user_id: str = Depends(get_current_user)
):
    await update_duration(
        skip_payload.history_id,
        skip_payload.duration_played,
        session_maker=session_maker,
    )
    return {"status": "ok"}


@musiql_api_router.get("/musiql/library/add/{uri}")
async def add_to_library(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user)
):
    new_record = UserLirbary(
        user_id=user_id,
        record_id=uri
    )

    async with session_maker() as session, session.begin():
        session.add(new_record)
    
    return {"status": f"successfully added {uri} to {user_id}'s library"}


@musiql_api_router.get("/musiql/library/remove/{uri}")
async def remove_from_library(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user)
):
    stmt = delete(UserLirbary).where(
        UserLirbary.user_id == user_id,
        UserLirbary.record_id == uri
    )

    async with session_maker() as session, session.begin():
        await session.execute(stmt)
    
    return {"status": f"successfully removed {uri} from {user_id}'s library"}


@musiql_api_router.get("/musiql/sample/{uri}")
async def sample_song(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    recommendation_api: GraphAMP = Depends(get_recommendation_api),
    user_id: str = Depends(get_current_user)
):
    states: List[str] = recommendation_api.preempt(uri)
    if not states:
        return []

    stmt = select(MusiqlRepository).where(MusiqlRepository.uri.in_(states))

    async with session_maker() as session:
        result = await session.execute(stmt)
        sample_records = result.scalars().all()

        if not sample_records:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="no records found"
            )

        by_uri = {r.uri: r for r in sample_records}
        ordered_records = [by_uri[u] for u in states if u in by_uri]

        content = [
            {"uri": record.uri, "title": record.title, "artists": record.artists}
            for record in ordered_records
        ]

        return content
