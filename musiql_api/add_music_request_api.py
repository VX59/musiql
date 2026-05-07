import requests
import json
import pickle

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from database.models import (
    UploadJobs,
    MusiqlRepository,
    RecordArtistAssociation,
    Artists,
)
from authtoken_api import get_current_user

from utility import make_uri, SourceTypes, JobTypes, JobStatus
from database.db import get_session
from boto3_tools import S3, get_S3
from settings import Settings, get_settings
from .data_models import spotify_item, spotify_playlist


MAX_RETRIES = 3


class ExpiredAccessToken(Exception):
    pass


settings: Settings = get_settings()


def refresh_access_token(code_holder, client_id, client_secret):
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": code_holder["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )

    code_holder["access_token"] = response.json()["access_token"]

    with open("internal_tools/codes.json", "w") as writer:
        json.dump(code_holder, writer)


def save_track(code_holder, record_id, job_uri, retries=0):
    if retries >= MAX_RETRIES:
        raise HTTPException(
            status_code=500, detail="failed to refresh spotify access token"
        )

    headers = {"Authorization": f"Bearer {code_holder['access_token']}"}
    url = f"https://api.spotify.com/v1/tracks/{record_id}"

    response = requests.get(url, headers=headers)

    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        refresh_access_token(
            code_holder,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
        )
        save_track(
            code_holder=code_holder,
            record_id=record_id,
            job_uri=job_uri,
            retries=retries + 1,
        )

    data = response.json()

    if "error" in data:
        raise HTTPException(status_code=response.status_code, detail=data["error"])

    item_obj: list[spotify_item] = [spotify_item.create_from_dict(data)]
    outpath = f"add_music_jobs/{job_uri}.dump"
    with open(outpath, "wb") as f:
        pickle.dump(item_obj, f)

    return outpath, 1


def save_playlist(code_holder, playlist_id, job_uri, retries=0):
    if retries >= MAX_RETRIES:
        raise HTTPException(
            status_code=500, detail="failed to refresh spotify access token"
        )

    headers = {"Authorization": f"Bearer {code_holder['access_token']}"}
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}"

    all_items = []

    while url:
        print(url)
        response = requests.get(url, headers=headers)

        if response.status_code == 401:
            refresh_access_token(
                code_holder,
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret,
            )
            save_playlist(
                code_holder, playlist_id, job_uri=job_uri, retries=retries + 1
            )

        data = response.json()

        if "error" in data:
            raise HTTPException(status_code=response.status_code, detail=data["error"])

        if not all_items:
            next = data["items"]["next"]
            items = data["items"]["items"]
        else:
            next = data["next"]
            items = data["items"]

        all_items.extend(items)
        url = next

    items_obj: list[spotify_item] = [
        spotify_item.create_from_dict(entry["item"]) for entry in all_items
    ]

    outpath = f"add_music_jobs/{job_uri}.dump"

    with open(outpath, "wb") as f:
        pickle.dump(items_obj, f)

    return outpath, len(all_items)


upload_job_router = APIRouter()


class CreateUploadJob(BaseModel):
    source_uri: str
    source_type: SourceTypes
    name: str
    association: str


class ExternalSearch(BaseModel):
    source_types: list[SourceTypes]
    limit: int = 5
    search_term: str


class ReportRecordingPayload(BaseModel):
    uri: str


def do_external_search(code_holder, search: ExternalSearch, retries=0):
    if retries >= MAX_RETRIES:
        raise HTTPException(
            status_code=500, detail="failed to refresh spotify access token"
        )
    headers = {"Authorization": f"Bearer {code_holder['access_token']}"}
    params = {
        "q": search.search_term,
        "type": ",".join(search.source_types),
        "limit": min(search.limit + 5, 50),
    }
    url = "https://api.spotify.com/v1/search"

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        refresh_access_token(
            code_holder,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
        )
        do_external_search(
            code_holder=code_holder,
            search=search,
            retries=retries + 1,
        )

    data = response.json()

    if "error" in data:
        raise HTTPException(status_code=response.status_code, detail=data["error"])

    search_result = {}

    for source_type in search.source_types:
        match source_type:
            case SourceTypes.track:
                search_result["tracks"] = []

                for track in data["tracks"]["items"]:
                    if track is None:
                        continue
                    if len(search_result["tracks"]) >= search.limit:
                        break
                    track_obj = spotify_item.create_from_dict(track)
                    track_id = track_obj.uri.split(":")[-1]
                    cleaned_track = {
                        "external_uri": track_id,
                        "title": track_obj.name,
                        "album": track_obj.album.name,
                        "artists": [artist.name for artist in track_obj.artists],
                    }

                    search_result["tracks"].append(cleaned_track)

            case SourceTypes.album:
                pass
            case SourceTypes.playlist:
                search_result["playlists"] = []

                for playlist in data["playlists"]["items"]:
                    if playlist is None:
                        continue
                    if len(search_result["playlists"]) >= search.limit:
                        break

                    playlist_obj: spotify_playlist = spotify_playlist.create_from_dict(
                        playlist
                    )
                    playlist_id = playlist_obj.uri.split(":")[-1]
                    cleaned_playlist = {
                        "external_uri": playlist_id,
                        "title": playlist_obj.name,
                        "owner": playlist_obj.owner.display_name
                        or playlist_obj.owner.id,
                        "items": playlist_obj.items.get("total"),
                    }

                    search_result["playlists"].append(cleaned_playlist)

            case _:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"unsupported type {source_type}",
                )

    return search_result


@upload_job_router.post("/external/search/", response_model=None)
async def external_search(
    payload: ExternalSearch,
    user_id=Depends(get_current_user),
):

    with open("internal_tools/codes.json", "r") as reader:
        code_holder = json.load(reader)

    search_result: dict = do_external_search(code_holder, payload)

    return search_result


@upload_job_router.post("/report/recording")
async def report_recording(
    payload: ReportRecordingPayload,
    session_maker: sessionmaker = Depends(get_session),
    s3_api: S3 = Depends(get_S3),
    user_id=Depends(get_current_user),
):
    with open("internal_tools/codes.json", "r") as reader:
        code_holder = json.load(reader)

    async with session_maker() as session:
        stmt = select(MusiqlRepository).where(MusiqlRepository.uri == payload.uri)
        result = await session.execute(stmt)

        reported_record: MusiqlRepository = result.scalar_one_or_none()

        if reported_record is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{payload.uri} does not exist",
            )

        external_uri = reported_record.external_uri.split(":")[-1]

        check_reported = select(UploadJobs).where(
            UploadJobs.source_id == external_uri,
            UploadJobs.job_type == JobTypes.correction,
            UploadJobs.status not in [JobStatus.finished, JobStatus.failed],
        )

        result = await session.execute(check_reported)

        job: UploadJobs = result.scalar_one_or_none()
        if job is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{job.status} {payload.source_type} {JobTypes.correction} job already exists for uri {payload.uri}",
            )

    job_uri = f"job:{make_uri()}"

    out_path, subtasks = save_track(
        code_holder=code_holder, record_id=external_uri, job_uri=job_uri
    )

    async with session_maker() as session:
        stmt = (
            select(Artists)
            .select_from(MusiqlRepository)
            .outerjoin(
                RecordArtistAssociation,
                MusiqlRepository.uri == RecordArtistAssociation.record_uri,
            )
            .outerjoin(Artists, RecordArtistAssociation.artist_uri == Artists.uri)
            .where(MusiqlRepository.uri == reported_record.uri)
        )

        result = await session.execute(stmt)
        artists: list[Artists] = result.scalars().all()

        association = ", ".join([artist.artist_name for artist in artists])

        job = UploadJobs(
            uri=job_uri,
            source_type=SourceTypes.track,
            job_type=JobTypes.correction,
            source_id=external_uri,
            subtasks=subtasks,
            progress=0,
            status=JobStatus.pending,
            requestor=user_id,
            name=reported_record.title,
            association=association,
        )

        session.add(job)

        await session.commit()

    upload_id, parts = s3_api.upload_object_from_path(out_path, out_path)
    s3_api.commit_multipart_upload(
        obj_path=out_path, key=out_path, upload_id=upload_id, parts=parts
    )

    return {
        f"successfully queue new {SourceTypes.track} {JobTypes.correction} job for uri {payload.uri}"
    }


@upload_job_router.post("/upload/music/", response_model=None)
async def add_music(
    payload: CreateUploadJob,
    session_maker: sessionmaker = Depends(get_session),
    user_id=Depends(get_current_user),
    s3_api: S3 = Depends(get_S3),
):

    with open("internal_tools/codes.json", "r") as reader:
        code_holder = json.load(reader)

    async with session_maker() as session:
        check_finished = select(UploadJobs).where(
            UploadJobs.source_id == payload.source_uri,
            UploadJobs.status == JobStatus.finished,
        )

        result = await session.execute(check_finished)
        if result.first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"already added {payload.source_type} {payload.source_uri}",
            )

        check_job = select(UploadJobs).where(
            UploadJobs.source_type == payload.source_type,
            UploadJobs.source_id == payload.source_uri,
            UploadJobs.status != JobStatus.finished,
        )

        result = await session.execute(check_job)
        job: UploadJobs = result.scalar_one_or_none()
        if job is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{payload.source_type} {JobTypes.integration} job already exists for external uri {payload.source_uri}",
            )

    job_uri = f"job:{make_uri()}"

    match payload.source_type:
        case SourceTypes.playlist:
            out_path, subtasks = save_playlist(
                code_holder=code_holder, playlist_id=payload.source_uri, job_uri=job_uri
            )
        case SourceTypes.track:
            out_path, subtasks = save_track(
                code_holder=code_holder, record_id=payload.source_uri, job_uri=job_uri
            )
        case _:
            raise HTTPException(
                status_code=400, detail=f"invalid source type {payload.source_type}"
            )

    async with session_maker() as session:
        job = UploadJobs(
            uri=job_uri,
            source_type=payload.source_type,
            job_type=JobTypes.integration,
            source_id=payload.source_uri,
            subtasks=subtasks,
            progress=0,
            status=JobStatus.pending,
            requestor=user_id,
            name=payload.name,
            association=payload.association,
        )

        session.add(job)

        await session.commit()

    upload_id, parts = s3_api.upload_object_from_path(out_path, out_path)
    s3_api.commit_multipart_upload(
        obj_path=out_path, key=out_path, upload_id=upload_id, parts=parts
    )

    return {
        f"successfully queued new {payload.source_type} {JobTypes.integration} job for external uri {payload.source_uri}"
    }


@upload_job_router.get("/upload/jobs")
async def get_jobs(
    session_maker: sessionmaker = Depends(get_session),
    user_id=Depends(get_current_user),
):
    async with session_maker() as session:
        stmt = (
            select(UploadJobs)
            .where(
                UploadJobs.requestor == user_id,
                UploadJobs.job_type == JobTypes.integration,
            )
            .order_by(UploadJobs.dttm.desc())
        )
        result = await session.execute(stmt)
        jobs: list[UploadJobs] = result.scalars().all()

        if not jobs:
            return {"no jobs for current user"}

        response = [
            {
                "job type": job.job_type,
                "source type": job.source_type,
                "subtasks": job.subtasks,
                "progress": job.progress,
                "status": job.status,
                "date requested": job.dttm,
                "name": job.name,
                "association": job.association,
            }
            for job in jobs
        ]

        return response
