import requests
from datetime import datetime
import os
import json
import shutil
from musiql_api.data_models import spotify_item
from settings import Settings, get_settings

import subprocess
import signal

import time

from utility import (
    logger,
    JobStatus,
    JobTypes,
    make_uri,
    retry,
)
from database.db import get_session
from database.models import (
    MusiqlRepository,
    Artists,
    Albums,
    AlbumArtistAssociation,
    RecordArtistAssociation,
    Users,
    UserLirbary,
    UploadJobs,
)
from boto3_tools import S3, get_S3
from tqdm import tqdm
from sqlalchemy.orm import sessionmaker
from sqlalchemy import update
from sqlalchemy.future import select
from sqlalchemy import func
import asyncio
import pickle
from typing import List, Optional


settings: Settings = get_settings()
s3_api: S3 = get_S3()
session_maker: sessionmaker = get_session()


def get_devices(code_holder):
    @retry(code_holder)
    def request_get_devices():
        return requests.get(
            url="https://api.spotify.com/v1/me/player/devices",
            headers={"Authorization": f"Bearer {code_holder['access_token']}"},
        )

    response = request_get_devices()
    print(response.json())

    return response


def get_computer_device_id(code_holder, name="jacob-server") -> str:
    response = get_devices(code_holder)
    devices = response.json().get("devices", [])
    for d in devices:
        if d.get("name") == name:
            return d["id"]

    raise RuntimeError(f"{name} found among: {[d['name'] for d in devices]}")


device_id = None


def activate_device(code_holder, device_id):
    @retry(code_holder)
    def request_activate_device():
        return requests.put(
            "https://api.spotify.com/v1/me/player",
            headers={
                "Authorization": f"Bearer {code_holder['access_token']}",
                "Content-Type": "application/json",
            },
            json={"device_ids": [device_id], "play": False},
        )

    request_activate_device()


def trigger_playback(code_holder, uri: str, retries=0):
    @retry(code_holder)
    def request_trigger_playback():
        return requests.put(
            url="https://api.spotify.com/v1/me/player/play",
            params={"device_id": device_id},
            headers={
                "Authorization": f"Bearer {code_holder['access_token']}",
                "Content-Type": "application/json",
            },
            json={
                "uris": [uri],
            },
        )

    request_trigger_playback()


def record_virtual_audio(output_file: str):
    cmd = ["parecord", "-d", "virtual_sink.monitor", output_file]
    proc = subprocess.Popen(cmd)
    return proc


def wait_until_playing(code_holder, uri, timeout=15, poll_interval=0.5):
    @retry(code_holder)
    def poll():
        return requests.get(
            "https://api.spotify.com/v1/me/player",
            headers={"Authorization": f"Bearer {code_holder['access_token']}"},
        )

    deadline = time.time() + timeout
    while time.time() < deadline:
        r = poll()
        if not r.content:
            time.sleep(poll_interval)
            continue
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", poll_interval))
            time.sleep(retry_after)
            continue
        data = r.json()
        if data and data.get("is_playing") and data["item"]["uri"] == uri:
            return data.get("progress_ms", 0)
        time.sleep(poll_interval)

    return 0


async def scrape_records(code_holder, job: UploadJobs):
    metadata_obj_key = f"add_music_jobs/{job.uri}.dump"
    file_stream = s3_api.pull_obj_stream(metadata_obj_key)
    data = file_stream.read()

    record_list: list[spotify_item] = pickle.loads(data)
    records_progress: list[spotify_item] = record_list[job.progress :]

    activate_device(code_holder=code_holder, device_id=device_id)

    for i, record in tqdm(enumerate(records_progress)):
        external_record_uri = record.uri

        if job.job_type == JobTypes.integration:
            async with session_maker() as session:
                check_record = select(MusiqlRepository).where(
                    MusiqlRepository.external_uri == external_record_uri,
                )

                result = await session.execute(check_record)
                if result.first() is not None:
                    print(
                        f"record {record.name} by {str([a.name for a in record.artists])} exists -> skip recording"
                    )

                    if job.progress < job.subtasks:
                        job.progress += 1

                    if job.progress == job.subtasks:
                        job.status = "finished"

                    session.add(job)

                    await session.commit()

                    continue

            internal_record_uri = f"record:{make_uri()}"

        elif job.job_type == JobTypes.correction:
            async with session_maker() as session:
                stmt = (
                    select(MusiqlRepository)
                    .join(
                        UploadJobs,
                        func.split_part(MusiqlRepository.external_uri, ":", 3)
                        == UploadJobs.source_id,
                    )
                    .where(UploadJobs.uri == job.uri)
                )

                result = await session.execute(stmt)
                reported_record: MusiqlRepository = result.scalar_one_or_none()
                
                if reported_record is None:
                    raise ValueError(
                        f"no record linked to {JobTypes.correction} uri {job.uri}"
                    )

            internal_record_uri = reported_record.uri

        else:
            raise ValueError(f"unsupported job type {job.job_type}")

        if job.status == JobStatus.failed:
            async with session_maker() as session:
                stmt = (
                    update(UploadJobs)
                    .where(
                        UploadJobs.uri == job.uri,
                    )
                    .values(status=JobStatus.retrying)
                )

                await session.execute(stmt)
                await session.commit()

        obj_key = f"musiql_dump/{internal_record_uri}.wav"

        os.makedirs("musiql_dump", exist_ok=True)
        t_rec_start = time.time()
        proc = record_virtual_audio(output_file=obj_key)
        time.sleep(1.0)
        trigger_playback(code_holder, record.uri)
        progress_ms = wait_until_playing(code_holder, record.uri)
        t_detected = time.time()

        remaining_s = (record.duration_ms - progress_ms) / 1000 + 1.0
        try:
            time.sleep(remaining_s)
        finally:
            proc.send_signal(signal.SIGINT)
            proc.wait()

        trim_start_s = max(0.0, (t_detected - progress_ms / 1000) - t_rec_start)
        tmp_key = obj_key + ".tmp.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                f"./{obj_key}",
                "-ss",
                str(trim_start_s),
                "-t",
                str(record.duration_ms / 1000),
                f"./{tmp_key}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.replace(tmp_key, obj_key)

        # commit after populating db tables succeeds
        upload_id, parts = s3_api.upload_object_from_path(
            obj_path=obj_key,
            key=obj_key,
        )

        if job.job_type == JobTypes.integration:
            artist_db_objs: list[Artists] = []
            for artist in record.artists:
                obj = Artists(
                    uri=f"artist:{make_uri()}",
                    artist_name=artist.name,
                    external_uri=artist.uri,
                )
                artist_db_objs.append(obj)

            if record.album.release_date_precision == "year":
                release_date = datetime(
                    year=int(record.album.release_date),
                    month=1,
                    day=1,
                )
            elif record.album.release_date_precision == "day":
                release_date = datetime.fromisoformat(record.album.release_date)

            else:
                raise ValueError(
                    f"unsupported release date precision {record.album.release_date_precision}"
                )

            album_db_obj = Albums(
                uri=f"album:{make_uri()}",
                album_name=record.album.name,
                release_date=release_date,
                release_date_precision=record.album.release_date_precision,
                total_tracks=record.album.total_tracks,
                cover_preview_url=record.album.images[2].get("url"),
                cover_thumbnail_url=record.album.images[1].get("url"),
                cover_full_size_url=record.album.images[0].get("url"),
                external_uri=record.album.uri,
            )

            async with session_maker() as session:
                for i, artist in enumerate(artist_db_objs):
                    check_artist = select(Artists).where(
                        Artists.external_uri == artist.external_uri
                    )

                    result = await session.execute(check_artist)
                    artist_obj: Artists = result.scalars().first()
                    if artist_obj is None:
                        session.add(artist)
                    else:
                        artist_db_objs[i] = artist_obj

                check_album = select(Albums).where(
                    Albums.external_uri == album_db_obj.external_uri
                )

                result = await session.execute(check_album)
                album_obj = result.scalars().first()
                if album_obj is None:
                    session.add(album_db_obj)
                else:
                    album_db_obj = album_obj

                await session.commit()

                check_record = select(MusiqlRepository).where(
                    MusiqlRepository.external_uri == external_record_uri
                )

                result = await session.execute(check_record)
                if result.first() is None:
                    record_db_obj = MusiqlRepository(
                        uri=internal_record_uri,
                        album_uri=album_db_obj.uri,
                        title=record.name,
                        mime="audio/mpeg",
                        added_by="derosaj",
                        duration_ms=record.duration_ms,
                        external_uri=external_record_uri,
                    )
                    session.add(record_db_obj)

                await session.commit()

                for artist in artist_db_objs:
                    check_ra_assoc = (
                        select(MusiqlRepository, Artists)
                        .join(
                            RecordArtistAssociation,
                            MusiqlRepository.uri == RecordArtistAssociation.record_uri,
                        )
                        .join(
                            Artists, RecordArtistAssociation.artist_uri == Artists.uri
                        )
                    ).where(
                        MusiqlRepository.external_uri == record_db_obj.external_uri,
                        Artists.external_uri == artist.external_uri,
                    )

                    result = await session.execute(check_ra_assoc)
                    if result.first() is None:
                        robj = RecordArtistAssociation(
                            record_uri=record_db_obj.uri, artist_uri=artist.uri
                        )
                        session.add(robj)

                    check_aa_assoc = (
                        select(Albums, Artists)
                        .join(
                            AlbumArtistAssociation,
                            Albums.uri == AlbumArtistAssociation.album_uri,
                        )
                        .join(Artists, AlbumArtistAssociation.artist_uri == Artists.uri)
                    ).where(
                        Albums.external_uri == album_db_obj.external_uri,
                        Artists.external_uri == artist.external_uri,
                    )

                    result = await session.execute(check_aa_assoc)
                    if result.first() is None:
                        aobj = AlbumArtistAssociation(
                            album_uri=album_db_obj.uri, artist_uri=artist.uri
                        )
                        session.add(aobj)

                check_ur_assoc = (
                    select(Users, MusiqlRepository)
                    .join(UserLirbary, Users.uri == UserLirbary.user_id)
                    .join(
                        MusiqlRepository, UserLirbary.record_id == MusiqlRepository.uri
                    )
                    .where(
                        MusiqlRepository.external_uri == record_db_obj.external_uri,
                        UserLirbary.user_id == job.requestor,
                    )
                )

                result = await session.execute(check_ur_assoc)
                if result.first() is None:
                    urobj = UserLirbary(
                        user_id=job.requestor, record_id=record_db_obj.uri
                    )

                    session.add(urobj)

                await session.commit()

        async with session_maker() as session:
            if job.progress < job.subtasks:
                job.progress += 1

            if job.progress == job.subtasks:
                job.status = JobStatus.finished 
            session.add(job)

            await session.commit()

        s3_api.commit_multipart_upload(
            obj_path=obj_key, key=obj_key, upload_id=upload_id, parts=parts
        )

        s3_api.delete_object(metadata_obj_key)


async def collect_jobs() -> Optional[List[UploadJobs]]:
    async with session_maker() as session:
        stmt = (
            select(UploadJobs)
            .where(UploadJobs.status != JobStatus.finished)
            .order_by(UploadJobs.subtasks.asc())
        )
        result = await session.execute(stmt)

        jobs: List[UploadJobs] = result.scalars().all()

        if not jobs:
            logger.info("no unfinished jobs found")
            return None

        return jobs


async def main():

    with open("internal_tools/codes.json", "r") as reader:
        code_holder = json.load(reader)

    get_devices(code_holder)

    subprocess.run(["pactl", "set-default-sink", "virtual_sink"], check=True)
    print("Default sink set to virtual_sink")

    global device_id
    device_id = get_computer_device_id(code_holder)
    print(f"Using device: {device_id}")
    activate_device(code_holder, device_id)

    while True:
        if not (jobs := await collect_jobs()):
            time.sleep(20)

        for job in jobs:
            try:
                await scrape_records(code_holder=code_holder, job=job)
            except Exception as e:
                async with session_maker() as session:
                    job.status = JobStatus.failed
                    session.add(job)
                    await session.commit()

                logger.exception(e)
            finally:
                shutil.rmtree("music_dump")
                os.mkdir("music_dump")

if __name__ == "__main__":
    asyncio.run(main())
