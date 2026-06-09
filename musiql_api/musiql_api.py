from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status, Depends
from settings import Settings, get_settings
from database.db import get_session
from boto3_tools import S3, get_S3
from fastapi.responses import HTMLResponse, JSONResponse
from database.models import (
    MusiqlRepository,
    MusiqlHistory,
    Users,
    UserLirbary,
    Models,
    Artists,
    Albums,
    RecordArtistAssociation,
    AlbumArtistAssociation,
)
from utility import timer_log
from sqlalchemy import update, or_, Select, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from datetime import datetime, timezone
from .models_api import GraphAMP, get_recommendation_api
from typing import List, Optional
from enum import Enum
from authtoken_api import get_current_user
import asyncio

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
        uri=uri,
        user_id=user_id,
        duration_played=1.0,
        listened_at=datetime.now(timezone.utc),
    )

    session.add(new_record)
    await session.commit()

    return new_record.id


@musiql_api_router.get("/serve/{uri}")
async def serve_record(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    s3_service: S3 = Depends(get_S3),
    user_id: str = Depends(get_current_user),
):
    stmt = select(MusiqlRepository).where(MusiqlRepository.uri == uri)
    async with session_maker() as session:
        async with timer_log(label="serve song", extra={"user_id": user_id}):
            result = await session.execute(stmt)

        record = result.scalars().first()

        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="record not found"
            )

        history_id = await track_history(record.uri, user_id, session)
        filename = f"{record.uri}.wav"
        s3_key = f"musiql_dump/{filename}"

        async with timer_log(label="get presigned url", extra={"user_id": user_id}):
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
    album = "album"


def parse_search_query(search_term: str, user_id) -> Optional[Select]:
    command = search_term[1:] if search_term.split(" ")[0][0] == "@" else None
    print(command)
    stmt = None

    match command:
        case QueryLang.library:
            stmt = (
                select(MusiqlRepository, Artists, Albums)
                .select_from(UserLirbary)
                .outerjoin(
                    MusiqlRepository, UserLirbary.record_id == MusiqlRepository.uri
                )
                .outerjoin(Albums, MusiqlRepository.album_uri == Albums.uri)
                .outerjoin(
                    RecordArtistAssociation,
                    MusiqlRepository.uri == RecordArtistAssociation.record_uri,
                )
                .outerjoin(Artists, RecordArtistAssociation.artist_uri == Artists.uri)
                .order_by(MusiqlRepository.created.desc())
            ).where(UserLirbary.user_id == user_id)

        case _:  # standard repo search
            stmt = (
                select(MusiqlRepository, Artists, Albums)
                .select_from(MusiqlRepository)
                .outerjoin(Albums, MusiqlRepository.album_uri == Albums.uri)
                .outerjoin(
                    RecordArtistAssociation,
                    MusiqlRepository.uri == RecordArtistAssociation.record_uri,
                )
                .outerjoin(Artists, RecordArtistAssociation.artist_uri == Artists.uri)
                .where(
                    or_(
                        MusiqlRepository.title.ilike(f"%{search_term}%"),
                        MusiqlRepository.added_by.ilike(f"%{search_term}%"),
                        Albums.album_name.ilike(f"%{search_term}%"),
                        Artists.artist_name.ilike(f"%{search_term}%"),
                    )
                )
                .order_by(MusiqlRepository.created.desc())
            )

    return stmt, command


@musiql_api_router.post("/search/advanced", response_model=None)
async def advanced_search_songs(
    payload: AdvancedSearchPayload = None,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    stmt, command = parse_search_query(payload.search_term, user_id)
    if stmt is None:
        raise ValueError("search query parser failed to generate a statement")

    async with session_maker() as session:
        async with timer_log(label="search musiql", extra={"user_id": user_id}):
            result = await session.execute(stmt)

        records = result.all()

        if records is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="no records found"
            )

        if command != QueryLang.library:
            identity_stmt = (
                select(MusiqlRepository, Artists, Albums)
                .select_from(UserLirbary)
                .outerjoin(
                    MusiqlRepository, UserLirbary.record_id == MusiqlRepository.uri
                )
                .outerjoin(Albums, MusiqlRepository.album_uri == Albums.uri)
                .outerjoin(
                    RecordArtistAssociation,
                    MusiqlRepository.uri == RecordArtistAssociation.record_uri,
                )
                .outerjoin(Artists, RecordArtistAssociation.artist_uri == Artists.uri)
                .where(
                    UserLirbary.user_id == user_id,
                    or_(
                        MusiqlRepository.title.ilike(f"%{payload.search_term}%"),
                        MusiqlRepository.added_by.ilike(f"%{payload.search_term}%"),
                        Albums.album_name.ilike(f"%{payload.search_term}%"),
                        Artists.artist_name.ilike(f"%{payload.search_term}%"),
                    ),
                )
                .order_by(MusiqlRepository.created.desc())
            )

            async with timer_log(
                label="filter search in library",
                extra={"user_id": user_id},
            ):
                result = await session.execute(identity_stmt)

            identity_records = result.all()

            identity_uris = [r.uri for r, _, _ in identity_records]

            in_identity = [
                True if uri in identity_uris else False
                for uri in [r.uri for r, _, _ in records]
            ]

            search_context = list(zip(records, in_identity))

        else:
            search_context = list(zip(records, [True] * len(records)))

    record_uris = [rec.uri for rec, _, _ in records]

    response = {
        "results": [
            {
                "uri": rec.uri,
                "album_uri": alb.uri,
                "title": rec.title,
                "album": alb.album_name,
                "duration_ms": rec.duration_ms,
                "artists": [
                    {"uri": artist.uri, "name": artist.artist_name}
                    for record, artist, _ in records
                    if record.uri == rec.uri and artist is not None
                ],
                "added_by": rec.added_by,
                "in_library": in_identity,
                "preview_url": alb.cover_preview_url,
                "thumbnail_url": alb.cover_thumbnail_url,
            }
            for i, ((rec, _, alb), in_identity) in enumerate(search_context)
            if rec.uri not in record_uris[:i]
        ],
    }

    response["num_results"] = len(response["results"])

    if len(records) == 1 and payload.history_id > 0:
        await update_duration(
            payload.history_id, payload.duration_played, session_maker=session_maker
        )

    return response


@musiql_api_router.get("/", response_class=HTMLResponse)
async def serve_player(
    settings: Settings = Depends(get_settings),
):
    html_path = "./musiql-desktop/dist/index.html"

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    env_script = (
        "<script>window.__ENV__ = {"
        f'"MUSIQL_API_URL": "{settings.musiql_api_url}", '
        "};</script>"
    )
    html_content = html_content.replace("<!-- __ENV__ -->", env_script)

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


@musiql_api_router.post("/log/engagement/")
async def log_engagement(
    skip_payload: SkipPayload,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    await update_duration(
        skip_payload.history_id,
        skip_payload.duration_played,
        session_maker=session_maker,
    )
    return {"status": "ok"}


@musiql_api_router.get("/library/add/{uri}")
async def add_to_library(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    new_record = UserLirbary(user_id=user_id, record_id=uri)

    async with session_maker() as session, session.begin():
        session.add(new_record)

    return {"status": f"successfully added {uri} to {user_id}'s library"}


@musiql_api_router.get("/library/remove/{uri}")
async def remove_from_library(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    stmt = delete(UserLirbary).where(
        UserLirbary.user_id == user_id, UserLirbary.record_id == uri
    )

    async with session_maker() as session, session.begin():
        await session.execute(stmt)

    return {"status": f"successfully removed {uri} from {user_id}'s library"}


@musiql_api_router.get("/sample/{uri}")
async def sample_song(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    async with session_maker() as session:
        stmt = (
            select(Models.uri)
            .select_from(Users)
            .where(Users.uri == user_id)
            .join(Models, Users.uri == Models.user_id)
        )

        async with timer_log(label="get model", extra={"user_id": user_id}):
            result = await session.execute(stmt)

        model_uri = result.scalar_one_or_none()
        if model_uri is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="model not found"
            )

        async with timer_log(label="load model", extra={"user_id": user_id}):
            loop = asyncio.get_event_loop()
            recommendation_api: GraphAMP = await loop.run_in_executor(
                None, get_recommendation_api, model_uri
            )

        states: List[str] = recommendation_api.preempt(uri)
        if not states:
            return []

        stmt = (
            select(MusiqlRepository, Artists, Albums)
            .select_from(MusiqlRepository)
            .join(
                RecordArtistAssociation,
                MusiqlRepository.uri == RecordArtistAssociation.record_uri,
            )
            .join(Artists, RecordArtistAssociation.artist_uri == Artists.uri)
            .join(Albums, MusiqlRepository.album_uri == Albums.uri)
        ).where(MusiqlRepository.uri.in_(states))

        async with timer_log(label="sample song", extra={"user_id": user_id}):
            result = await session.execute(stmt)

        sample_records = result.all()

        if not sample_records:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="no records found"
            )

        by_uri = {rec.uri: (rec, art, alb) for rec, art, alb in sample_records}
        ordered_records = [by_uri[u] for u in states if u in by_uri]

        # goes into the queue
        content = [
            {
                "uri": rec.uri,
                "album_uri": alb.uri,
                "title": rec.title,
                "album": alb.album_name,
                "duration_ms": rec.duration_ms,
                "artists": [
                    {"uri": artist.uri, "name": artist.artist_name}
                    for record, artist, _ in sample_records
                    if record.uri == rec.uri
                ],
                "preview_url": alb.cover_preview_url,
                "thumbnail_url": alb.cover_thumbnail_url,
            }
            for i, (rec, _, alb) in enumerate(ordered_records)
            if rec.uri not in list(by_uri.values())[:i]
        ]

        return content


class SkipsResponse(BaseModel):
    uri: str
    skips: list[float]


@musiql_api_router.get("/skips/{uri}", response_model=SkipsResponse)
async def get_skips(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    stmt = select(MusiqlHistory).where(
        MusiqlHistory.user_id == user_id,
        MusiqlHistory.duration_played < 1,
        MusiqlHistory.uri == uri,
    )

    async with session_maker() as session:
        skips: list[MusiqlHistory] = (await session.execute(stmt)).scalars().all()
        print(skips)
        response = {"uri": uri, "skips": [skip.duration_played for skip in skips]}
        return response


@musiql_api_router.get("/album/{uri}")
async def get_album(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    album_stmt = (
        select(Albums, Artists)
        .select_from(Albums)
        .outerjoin(
            AlbumArtistAssociation, Albums.uri == AlbumArtistAssociation.album_uri
        )
        .outerjoin(Artists, AlbumArtistAssociation.artist_uri == Artists.uri)
        .where(Albums.uri == uri)
    )
    tracks_stmt = (
        select(MusiqlRepository, Artists, UserLirbary)
        .select_from(MusiqlRepository)
        .outerjoin(
            RecordArtistAssociation,
            MusiqlRepository.uri == RecordArtistAssociation.record_uri,
        )
        .outerjoin(Artists, RecordArtistAssociation.artist_uri == Artists.uri)
        .outerjoin(
            UserLirbary,
            (UserLirbary.record_id == MusiqlRepository.uri)
            & (UserLirbary.user_id == user_id),
        )
        .where(MusiqlRepository.album_uri == uri)
        .order_by(MusiqlRepository.created)
    )
    async with session_maker() as session:
        album_rows = (await session.execute(album_stmt)).all()
        if not album_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="album not found"
            )
        album = album_rows[0][0]
        album_artists = [
            {"uri": row[1].uri, "name": row[1].artist_name}
            for row in album_rows
            if row[1] is not None
        ]
        track_rows = (await session.execute(tracks_stmt)).all()

    seen: list[str] = []
    return {
        "title": album.album_name,
        "artists": album_artists,
        "preview_url": album.cover_preview_url,
        "cover_url": album.cover_full_size_url,
        "tracks": [
            {
                "uri": rec.uri,
                "album_uri": uri,
                "title": rec.title,
                "album": album.album_name,
                "artists": [
                    {"uri": r[1].uri, "name": r[1].artist_name}
                    for r in track_rows
                    if r[0].uri == rec.uri and r[1] is not None
                ],
                "preview_url": album.cover_preview_url,
                "cover_url": album.cover_full_size_url,
                "in_library": lib is not None,
            }
            for rec, _, lib in track_rows
            if rec.uri not in seen and not seen.append(rec.uri)
        ],
    }


@musiql_api_router.get("/artist/{uri}")
async def get_artist(
    uri: str,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    artist_stmt = select(Artists).where(Artists.uri == uri)
    albums_stmt = (
        select(Albums)
        .select_from(AlbumArtistAssociation)
        .join(Albums, AlbumArtistAssociation.album_uri == Albums.uri)
        .where(AlbumArtistAssociation.artist_uri == uri)
        .order_by(Albums.release_date.desc())
    )
    async with session_maker() as session:
        artist = (await session.execute(artist_stmt)).scalars().first()
        if artist is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="artist not found"
            )
        album_rows = (await session.execute(albums_stmt)).scalars().all()

    seen_albums: set[str] = set()
    return {
        "uri": artist.uri,
        "artist_name": artist.artist_name,
        "albums": [
            {
                "uri": alb.uri,
                "album_name": alb.album_name,
                "cover_thumbnail_url": alb.cover_thumbnail_url,
                "cover_url": alb.cover_full_size_url,
                "release_date": alb.release_date.isoformat()
                if alb.release_date is not None
                else None,
            }
            for alb in album_rows
            if alb.uri not in seen_albums and not seen_albums.add(alb.uri)
        ],
    }
