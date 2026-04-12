from sqlalchemy import update, func
from database.models import MusiqlRepository
from database.db import async_session
import asyncio

async def canonicalize_names():

    clean_stmt = (
        update(MusiqlRepository)
        .values(
            title=func.regexp_replace(
                func.regexp_replace(
                    func.regexp_replace(
                        MusiqlRepository.title,
                        r'^.*[-–]\s*',
                        ''
                    ),
                    r'[\(\[]\s*.*?(official|visualizer|video|audio|hd).*?[\)\]]',
                    '',
                    'i'
                ),
                r'^.*-\s*',
                ''
            )
        )
    )

    split_stmt = (
        update(MusiqlRepository)
        .values(
            title=func.regexp_replace(
                MusiqlRepository.title,
                r'^.*-\s*',
                ''
            )
        )
    )

    async with async_session() as session:
        await session.execute(clean_stmt)
        await session.execute(split_stmt)

        await session.commit()

async def main():
    await canonicalize_names()

if __name__ == "__main__":
    asyncio.run(main())