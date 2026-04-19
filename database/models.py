from sqlalchemy.dialects import postgresql
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import DateTime, func, ForeignKey
from datetime import datetime, timezone

Base = declarative_base()


class MusiqlRepository(Base):
    __tablename__ = "music_repository"

    uri: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(nullable=False)
    artists: Mapped[str] = mapped_column(nullable=False, index=True)
    filepath: Mapped[str] = mapped_column(nullable=False)
    hash: Mapped[bytes] = mapped_column(nullable=False)
    mime: Mapped[str] = mapped_column(nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        postgresql.JSONB(astext_type=sa.TEXT), nullable=False
    )
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=datetime.now(timezone.utc),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(nullable=True)


class MusiqlHistory(Base):
    __tablename__ = "music_history"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    uri: Mapped[str] = mapped_column(
        ForeignKey("music_repository.uri", ondelete="CASCADE"), nullable=False
    )
    duration_played: Mapped[float] = mapped_column(nullable=False)
    listened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Users(Base):
    __tablename__ = "users"
    uri: Mapped[str] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(nullable=False)
    password: Mapped[bytes] = mapped_column(nullable=False)
    access_level: Mapped[int] = mapped_column(nullable=False)


class UserLirbary(Base):
    __tablename__ = "libraries"
    user_id: Mapped[str] = mapped_column(primary_key=True)
    record_id: Mapped[str] = mapped_column(primary_key=True)


class Models(Base):
    __tablename__ = "models"
    uri: Mapped[str] = mapped_column(nullable=False, primary_key=True)
    user_id: Mapped[str] = mapped_column(nullable=False)
    model_name: Mapped[str] = mapped_column(nullable=False)


class ModelUpdates(Base):
    __tablename__ = "model_updates"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    dttm: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
