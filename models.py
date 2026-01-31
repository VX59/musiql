from sqlalchemy import Column, BigInteger, LargeBinary, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class MusiqlRepository(Base):
    __tablename__="music_repository"

    uri = Column(BigInteger, primary_key=True, index=True)
    title = Column(String, nullable=False)
    artists = Column(postgresql.JSONB, nullable=True)
    data = Column(LargeBinary, nullable=False)
    metadata_json = Column(postgresql.JSONB, nullable=True)