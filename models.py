from sqlalchemy.dialects import postgresql
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import DateTime, func, ForeignKey, String, BigInteger, Float
from datetime import datetime, timezone

Base = declarative_base()

class MusiqlRepository(Base):
    __tablename__="music_repository"

    uri : Mapped[str] = mapped_column(primary_key=True)
    title : Mapped[str] = mapped_column(nullable=False)
    artists : Mapped[str] = mapped_column(nullable=False)
    filepath : Mapped[str] = mapped_column(nullable=False)
    hash : Mapped[bytes] = mapped_column(nullable=False)
    mime : Mapped[str] = mapped_column(nullable=False)
    metadata_json : Mapped[dict] = mapped_column(postgresql.JSONB(astext_type=sa.TEXT), nullable=False)
    created : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=datetime.now(timezone.utc), nullable=False)
    index : Mapped[int] = mapped_column(nullable=False)
    
class MusiqlHistory(Base):
    __tablename__="music_history"
    id : Mapped[int] = mapped_column(primary_key=True, index=True)
    uri : Mapped[str] = mapped_column(ForeignKey("music_repository.uri", ondelete="CASCADE"),
                                      nullable=False)
    duration_played : Mapped[float] = mapped_column(nullable=False)
    listened_at : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
