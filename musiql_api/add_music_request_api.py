import requests
import base64
import json
import pickle
import webbrowser
import urllib.parse
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from database.models import UploadJobs
from authtoken_api import get_current_user
from enum import Enum

from utility import make_uri, SourceTypes, JobTypes, JobStatus
from database.db import get_session
from boto3_tools import (
    S3, get_S3,
    SQS, get_SQS
)
from settings import Settings, get_settings
from .data_models import spotify_item


MAX_RETRIES = 3
class ExpiredAccessToken(Exception):
    pass

settings:Settings = get_settings()

redirect_uri = "http://127.0.0.1:8888/callback"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self, code_holder):

        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        print("FULL CALLBACK:", self.path)

        if "error" in query:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Spotify auth error")
            return

        if "code" in query:
            code = query["code"][0]

            self.send_response(code=200)
            self.end_headers()
            self.wfile.write(b"OK - you can close this tab")

            access_token, refresh_token = self.get_bearer_token(code)

            code_holder["code"] = code
            code_holder["access_token"] = access_token
            code_holder["refresh_token"] = refresh_token

            return

        self.send_response(400)
        self.end_headers()


    def get_bearer_token(self, code):
        auth_header = base64.b64encode(
            f"{settings.spotify_client_id}:{settings.spotify_client_secret}".encode()
        ).decode()

        response = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri
            }
        )

        tokens = response.json()
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        return access_token, refresh_token


def run_server():
    server = HTTPServer(("127.0.0.1", 8888), Handler)
    print("Waiting for Spotify callback...")
    server.handle_request()
    server.server_close()

#threading.Thread(target=run_server, daemon=True).start()

def open_redirect(code_holder):

    scope = "user-read-private user-read-email user-modify-playback-state user-read-playback-state user-read-currently-playing"

    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": settings.spotify_client_id,
        "scope": scope,
        "redirect_uri": redirect_uri,
        "state": state,
        "show_dialog": True
    }

    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)

    webbrowser.open(auth_url)

    while code_holder.get("access_token") is None:
        time.sleep(0.2)
    
    with open("internal_tools/codes.json", "w") as writer:
        json.dump(code_holder, writer)

#open_redirect()

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
            status_code=500,
            detail="failed to refresh spotify access token"
        )

    headers={
        "Authorization": f"Bearer {code_holder['access_token']}"
    }
    url=f"https://api.spotify.com/v1/tracks/{record_id}"

    response = requests.get(url, headers=headers)

    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        refresh_access_token(
            code_holder,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret
        )
        save_track(
            code_holder=code_holder,
            record_id=record_id,
            job_uri=job_uri,
            retries=retries + 1
        )

    data = response.json()

    if "error" in data:
        raise HTTPException(
            status_code=response.status_code,
            detail=data["error"])

    item_obj:list[spotify_item] = [spotify_item.create_from_dict(data)]
    outpath = f"add_music_jobs/{job_uri}.dump"
    with open(outpath, "wb") as f:
        pickle.dump(item_obj, f)

    return outpath, 1


def save_playlist(code_holder, playlist_id, job_uri, retries=0):
    if retries >= MAX_RETRIES:
        raise HTTPException(
            status_code=500,
            detail="failed to refresh spotify access token"
        )
    
    headers={
        "Authorization": f"Bearer {code_holder['access_token']}"
    }
    url=f"https://api.spotify.com/v1/playlists/{playlist_id}"

    all_items = []

    while url:
        print(url)
        response = requests.get(url, headers=headers)

        if response.status_code == 401:
            refresh_access_token(
                code_holder,
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret
            )
            save_playlist(
                code_holder,
                playlist_id,
                job_uri=job_uri,
                retries=retries + 1
            )

        data = response.json()

        if "error" in data:
            raise HTTPException(
                status_code=response.status_code,
                detail=data["error"])

        if not all_items:
            next = data["items"]["next"]
            items = data["items"]["items"]
        else:
            next = data["next"]
            items = data["items"]

        all_items.extend(items)
        url = next


    items_obj:list[spotify_item] = [
            spotify_item.create_from_dict(entry["item"])
            for entry
            in all_items
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
    search_term: str


def do_external_search(
    code_holder,
    search_term,
    source_types:list[str],
    retries=0
):
    if retries >= MAX_RETRIES:
        raise HTTPException(
            status_code=500,
            detail="failed to refresh spotify access token"
        )
    headers = {
        "Authorization": f"Bearer {code_holder['access_token']}"
    }
    params = {
        "q": search_term,
        "type" : ",".join(source_types)
    }
    url=f"https://api.spotify.com/v1/search"

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        refresh_access_token(
            code_holder,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret
        )
        do_external_search(
            code_holder=code_holder,
            search_term=search_term,
            source_types=source_types,
            retries=retries + 1
        )
    
    data = response.json()

    if "error" in data:
        raise HTTPException(
            status_code=response.status_code,
            detail=data["error"])
    
    search_result = {}

    for source_type in source_types:
        match source_type:
            case SourceTypes.track:
                search_result["tracks"] = []

                for track in data["tracks"]["items"]:
                    track_obj = spotify_item.create_from_dict(track)
                    track_id = track_obj.uri.split(":")[-1]
                    cleaned_track = {
                        "external_uri": track_id,
                        "title": track_obj.name,
                        "album": track_obj.album.name,
                        "artists": [
                            artist.name for artist in track_obj.artists
                        ]
                    }

                    search_result["tracks"].append(cleaned_track)
                    
            case SourceTypes.album:
                pass
            case SourceTypes.playlist:
                pass
            case _:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"unsupported type {source_type}"
                )

    return search_result

@upload_job_router.post("/external/search/", response_model=None)
async def external_search(
    payload:ExternalSearch,
    user_id=Depends(get_current_user),
):
    
    with open("internal_tools/codes.json", "r") as reader:
        code_holder = json.load(reader)

    search_result:dict = do_external_search(
        code_holder,
        payload.search_term,
        payload.source_types
    )

    return search_result

@upload_job_router.post("/add/music/", response_model=None)
async def add_music(
    payload:CreateUploadJob,
    session_maker: sessionmaker = Depends(get_session),
    user_id = Depends(get_current_user),
    s3_api:S3 = Depends(get_S3),
    sqs_api:SQS = Depends(get_SQS)
):

    with open("internal_tools/codes.json", "r") as reader:
        code_holder = json.load(reader)

    async with session_maker() as session:
        check_finished = select(UploadJobs).where(
            UploadJobs.source_id == payload.source_uri,
            UploadJobs.status == "finished"
        )

        result = await session.execute(check_finished)
        if result.first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"already finished uploading playlist {payload.source_uri}"
            )

        check_job = select(UploadJobs).where(
            UploadJobs.source_type == payload.source_type,
            UploadJobs.source_id == payload.source_uri,
            UploadJobs.status != "finished"
        )

        result = await session.execute(check_job)
        job:UploadJobs = result.scalars().first()
        if job is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{payload.source_type} upload job already exists for external uri {payload.source_uri}"
            )

    job_uri = f"job:{make_uri()}"

    match payload.source_type:
        case SourceTypes.playlist:
            out_path, subtasks = save_playlist(
                code_holder=code_holder,
                playlist_id=payload.source_uri,
                job_uri=job_uri
            )
        case SourceTypes.track:
            out_path, subtasks = save_track(
                code_holder=code_holder,
                record_id=payload.source_uri,
                job_uri=job_uri
            )
        case _:
            raise HTTPException(
                status_code=400,
                detail=f"invalid source type {payload.source_type}"
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
            association=payload.association
        )

        session.add(job)

        await session.commit()
    
    upload_id, parts = s3_api.upload_object_from_path(out_path, out_path)
    s3_api.commit_multipart_upload(
        obj_path=out_path,
        key=out_path,
        upload_id=upload_id,
        parts=parts
    )
    
    sqs_api.send_message(body=job_uri)

    return {f"successfully queued new {payload.source_type} upload job for external uri {payload.source_uri}"}


@upload_job_router.get("/upload/jobs")
async def get_jobs(
    session_maker:sessionmaker = Depends(get_session),
    user_id = Depends(get_current_user)
):
    async with session_maker() as session:
        stmt = select(UploadJobs).where(UploadJobs.requestor == user_id)
        result = await session.execute(stmt)
        jobs:list[UploadJobs] = result.scalars().all()

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
                "association": job.association
            }
            for job in jobs
        ]

        return response