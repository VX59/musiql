import os
import requests
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

from utility import (
    SourceTypes,
    JobTypes,
    JobStatus,
    make_uri,
    retry,
    load_codes,
    timer_log,
)
from database.db import get_session
from boto3_tools import S3, get_S3
from settings import Settings, get_settings
from .data_models import spotify_item, spotify_playlist, spotify_album

settings: Settings = get_settings()


def save_track(code_holder, record_id, job_uri):
    @retry(code_holder, label="get track metadata")
    def save_track_request():
        headers = {"Authorization": f"Bearer {code_holder['access_token']}"}
        url = f"https://api.spotify.com/v1/tracks/{record_id}"
        return requests.get(url, headers=headers)

    response = save_track_request()
    data = response.json()

    if "error" in data:
        raise HTTPException(status_code=response.status_code, detail=data["error"])

    item_obj: list[spotify_item] = [spotify_item.create_from_dict(data)]
    s3_key = f"add_music_jobs/{job_uri}.dump"
    outpath = f"/tmp/{s3_key}"
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "wb") as f:
        pickle.dump(item_obj, f)

    return outpath, s3_key, 1


def save_album(code_holder, album_id, job_uri):
    all_items = []
    url = f"https://api.spotify.com/v1/albums/{album_id}"
    headers = {"Authorization": f"Bearer {code_holder['access_token']}"}

    @retry(code_holder, label="get album metadata chunk")
    def save_album_request():
        return requests.get(url, headers=headers)

    response = save_album_request()
    data = response.json()

    if "error" in data:
        raise HTTPException(status_code=response.status_code, detail=data["error"])

    items = data["tracks"]["items"]
    all_items.extend(items)

    album_stub = {
        k: data.get(k)
        for k in (
            "type",
            "album_type",
            "href",
            "id",
            "images",
            "name",
            "release_date",
            "release_date_precision",
            "uri",
            "artists",
            "external_urls",
            "total_tracks",
        )
    }

    items_obj: list[spotify_item] = [
        spotify_item.create_from_dict({**entry, "album": album_stub})
        for entry in all_items
    ]

    s3_key = f"add_music_jobs/{job_uri}.dump"
    outpath = f"/tmp/{s3_key}"
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "wb") as f:
        pickle.dump(items_obj, f)

    return outpath, s3_key, len(all_items)


def save_playlist(code_holder, playlist_id, job_uri):
    all_items = []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}"
    headers = {"Authorization": f"Bearer {code_holder['access_token']}"}

    while url:

        @retry(code_holder, label="get playlist metadata chunk")
        def save_playlist_request():
            return requests.get(url, headers=headers)

        response = save_playlist_request()
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

    s3_key = f"add_music_jobs/{job_uri}.dump"
    outpath = f"/tmp/{s3_key}"

    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "wb") as f:
        pickle.dump(items_obj, f)

    return outpath, s3_key, len(all_items)


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


def do_external_search(code_holder, search: ExternalSearch):
    @retry(code_holder, label="search spotify")
    def external_search_request():
        headers = {"Authorization": f"Bearer {code_holder['access_token']}"}
        params = {
            "q": search.search_term,
            "type": ",".join(search.source_types),
            "limit": min(search.limit + 5, 50),
        }
        url = "https://api.spotify.com/v1/search"

        return requests.get(url, headers=headers, params=params, timeout=10)

    response = external_search_request()
    data = response.json()

    if "error" in data:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=data["error"]
        )

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
                search_result["albums"] = []

                for album in data["albums"]["items"]:
                    if album is None:
                        continue
                    if len(search_result["albums"]) >= search.limit:
                        break
                    album_obj = spotify_album.create_from_dict(album)
                    album_id = album_obj.uri.split(":")[-1]
                    cleaned_album = {
                        "external_uri": album_id,
                        "title": album_obj.name,
                        "artists": [artist.name for artist in album_obj.artists],
                        "tracks": album_obj.total_tracks,
                    }

                    search_result["albums"].append(cleaned_album)

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
    s3_api: S3 = Depends(get_S3),
    user_id=Depends(get_current_user),
):
    code_holder = load_codes(s3_api)
    search_result: dict = do_external_search(code_holder, payload)
    return search_result


@upload_job_router.post("/report/recording")
async def report_recording(
    payload: ReportRecordingPayload,
    session_maker: sessionmaker = Depends(get_session),
    settings: Settings = Depends(get_settings),
    s3_api: S3 = Depends(get_S3),
    user_id=Depends(get_current_user),
):
    code_holder = load_codes(s3_api)

    async with session_maker() as session:
        stmt = select(MusiqlRepository).where(MusiqlRepository.uri == payload.uri)

        async with timer_log(label="check if song is already added"):
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

        async with timer_log(label="check if song is reported"):
            result = await session.execute(check_reported)

        job: UploadJobs = result.scalar_one_or_none()
        if job is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{job.status} {SourceTypes.track} {JobTypes.correction} job already exists for uri {payload.uri}",
            )

    job_uri = f"job:{make_uri()}"

    out_path, s3_key, subtasks = save_track(
        code_holder, record_id=external_uri, job_uri=job_uri
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

    upload_id, parts = s3_api.upload_object_from_path(out_path, s3_key)
    s3_api.commit_multipart_upload(
        obj_path=out_path, key=s3_key, upload_id=upload_id, parts=parts
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

    code_holder = load_codes(s3_api)

    async with session_maker() as session:
        print(f"spotify:track:{payload.source_uri}")
        if payload.source_type == SourceTypes.track:
            check_repository = select(MusiqlRepository).where(
                MusiqlRepository.external_uri == f"spotify:track:{payload.source_uri}"
            )

            async with timer_log(label="check repository for track"):
                result = await session.execute(check_repository)

            if result.first() is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{payload.source_uri} is already added to the repository",
                )

        check_integration_job = select(UploadJobs).where(
            UploadJobs.source_id == payload.source_uri,
            UploadJobs.job_type == JobTypes.integration,
        )

        async with timer_log(label="check if integration job for song exists"):
            result = await session.execute(check_integration_job)

        if result.first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{payload.source_type} {JobTypes.integration} job already exists for {payload.source_uri}",
            )

        job_uri = f"job:{make_uri()}"

        match payload.source_type:
            case SourceTypes.playlist:
                out_path, s3_key, subtasks = save_playlist(
                    code_holder,
                    playlist_id=payload.source_uri,
                    job_uri=job_uri,
                )
            case SourceTypes.album:
                out_path, s3_key, subtasks = save_album(
                    code_holder, album_id=payload.source_uri, job_uri=job_uri
                )
            case SourceTypes.track:
                out_path, s3_key, subtasks = save_track(
                    code_holder,
                    record_id=payload.source_uri,
                    job_uri=job_uri,
                )
            case _:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"invalid source type {payload.source_type}",
                )

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

    upload_id, parts = s3_api.upload_object_from_path(out_path, s3_key)
    s3_api.commit_multipart_upload(
        obj_path=out_path, key=s3_key, upload_id=upload_id, parts=parts
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
            )
            .order_by(UploadJobs.dttm.desc())
        )

        async with timer_log(label="get upload jobs", extra={"user_id": user_id}):
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
