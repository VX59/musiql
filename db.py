from sqlalchemy.orm import sessionmaker
from configparser import ConfigParser
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
config = ConfigParser()
config.read("alembic.ini")

DATABASE_URL = config["alembic"]["sqlalchemy.url"]

engine = create_async_engine(DATABASE_URL, echo=True)

async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)