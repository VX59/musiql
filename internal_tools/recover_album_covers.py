from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from database.models import Albums
from settings import get_settings, Settings
from musiql_api.add_music_request_api import refresh_access_token
from musiql_api.data_models import spotify_album
from database.db import get_session
import json
import asyncio
import requests

MAX_RETRIES = 3

settings: Settings = get_settings()
session_maker: sessionmaker = get_session()


async def recover_album_covers(code_holder, retries=0):
    if retries >= MAX_RETRIES:
        print("failed to refresh spotify access token")
        return

    async with session_maker() as session:
        stmt = select(Albums)
        result = await session.execute(stmt)

        albums: list[Albums] = result.scalars().all()

        if albums is None:
            raise ValueError("no albums")

    headers = {"Authorization": f"Bearer {code_holder['access_token']}"}

    for album in albums:
        uri = album.external_uri.split(":")[-1]
        url = f"https://api.spotify.com/v1/albums/{uri}"

        response = requests.get(url, headers=headers)

        print(response.json())

        if response.status_code == 401:
            refresh_access_token(
                code_holder,
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret,
            )

            await recover_album_covers(code_holder, retries=retries + 1)

        data = response.json()
        album_obj: spotify_album = spotify_album.create_from_dict(data)

        print(album_obj.images)

        album.cover_full_size_url = album_obj.images[0].get("url")
        album.cover_thumbnail_url = album_obj.images[1].get("url")
        album.cover_preview_url = album_obj.images[2].get("url")

        async with session_maker() as session:
            session.add(album)

            await session.commit()


async def main():
    with open("internal_tools/codes.json", "r") as reader:
        code_holder = json.load(reader)

    await recover_album_covers(code_holder)


if __name__ == "__main__":
    asyncio.run(main())
