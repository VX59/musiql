from sqlalchemy.future import select
from database.models import MusiqlRepository
from database.db import get_session
import asyncio
import os

def get_diff(db_uris:list):
    db_uris = set(db_uris)
    fs_uris = set(os.listdir("music_dump"))
    return list(fs_uris - db_uris)

async def clean_music_dump(session_maker = get_session()):

    stmt = select(MusiqlRepository.uri)
    db_uris = []
    async with session_maker() as session:
        async for uri in (await session.stream(stmt)).scalars():
            db_uris.append(uri+".mp3")

    diff = get_diff(db_uris)
    if not diff:
        print("no difference found between filesystem dump and database")
        return
    
    try:
        for file in diff: os.remove("music_dump/"+file)
    
        if diff := get_diff(db_uris):
            raise Exception(f"failed to remove extra files \n {diff}")

    except Exception as e:
        print(e)


async def main():
    await clean_music_dump()

if __name__ == "__main__":
    asyncio.run(main())