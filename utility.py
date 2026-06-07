import secrets
from enum import Enum
import base64
import requests
import json
from fastapi import HTTPException, status
from boto3_tools import S3, get_S3
from settings import Settings, get_settings
from time import perf_counter
from loguru import logger


def make_uri():
    uri = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")
    return uri


class AccessLevel:
    ADMIN = 0
    ELEVATED = 1
    STANDARD = 2


class SourceTypes(str, Enum):
    track = "track"
    album = "album"
    playlist = "playlist"


class JobTypes(str, Enum):
    integration = "integration"
    correction = "correction"


class JobStatus(str, Enum):
    failed = "failed"
    pending = "pending"
    retrying = "retrying"
    in_progress = "in progress"
    finished = "finished"


_codes_cache: dict | None = None


MAX_RETRIES = 3
CODES_S3_KEY = "spotify_utils/codes.json"


class ExpiredAccessToken(Exception):
    pass


def load_codes(s3_api: S3) -> dict:
    global _codes_cache
    if _codes_cache is None:
        stream = s3_api.pull_obj_stream(CODES_S3_KEY)
        _codes_cache = json.loads(stream.read())
    return _codes_cache


def refresh_access_token(code_holder):
    global _codes_cache

    settings: Settings = get_settings()
    s3_api: S3 = get_S3()

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": code_holder["refresh_token"],
            "client_id": settings.spotify_client_id,
            "client_secret": settings.spotify_client_secret,
        },
        timeout=10,
    )

    code_holder["access_token"] = response.json()["access_token"]
    _codes_cache = code_holder

    s3_api.put_object(json.dumps(code_holder).encode(), CODES_S3_KEY)


def retry(code_holder, label: str = ""):
    def decorator(func):
        def wrapper(retries=0):
            if retries >= MAX_RETRIES:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="failed to refresh spotify access token",
                )
            with timer_log(label=label):
                response = func()

            if response.status_code == status.HTTP_401_UNAUTHORIZED:
                refresh_access_token(code_holder)
                wrapper(retries + 1)

            return response

        return wrapper

    return decorator


class timer_log:
    def __init__(self, label: str = "", extra: dict = {}):
        self.label = "timer_log - " + label
        self.logger = logger
        self.extra = extra

    def _log(self, elapsed: float):
        msg = f"{self.label} took {elapsed * 1000:.2f}ms"
        if self.extra:
            msg += f" {self.extra}"
        self.logger.info(msg)

    def __enter__(self):
        self._start = perf_counter()
        return self

    def __exit__(self, *_):
        self._log(perf_counter() - self._start)

    async def __aenter__(self):
        self._start = perf_counter()
        return self

    async def __aexit__(self, *_):
        self._log(perf_counter() - self._start)
