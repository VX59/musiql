from sqlalchemy.orm import sessionmaker
from configparser import ConfigParser
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
config = ConfigParser()
config.read("alembic.ini")

DATABASE_URL = "postgresql+asyncpg://jacob:password@localhost:5432/musiql"

engine = create_async_engine(DATABASE_URL, echo=True)

async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)