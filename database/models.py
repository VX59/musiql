from sqlalchemy.dialects import postgresql
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import DateTime, func, ForeignKey
from datetime import datetime, timezone

Base = declarative_base()


class Albums(Base):
    __tablename__ = "albums"
    uri: Mapped[str] = mapped_column(primary_key=True)
    album_name: Mapped[str] = mapped_column(nullable=False, index=True)
    release_date: Mapped[datetime] = mapped_column(nullable=False)
    release_date_precision: Mapped[str] = mapped_column(nullable=True)
    total_tracks: Mapped[int] = mapped_column(nullable=False)
    cover_art_uri: Mapped[str] = mapped_column(nullable=True)
    external_uri: Mapped[str] = mapped_column(nullable=True)


class Artists(Base):
    __tablename__ = "artists"
    uri: Mapped[str] = mapped_column(primary_key=True)
    artist_name: Mapped[str] = mapped_column(nullable=False, index=True)
    external_uri: Mapped[str] = mapped_column(nullable=True)


class MusiqlRepository(Base):
    __tablename__ = "music_repository"

    uri: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(nullable=False, index=True)
    album_uri: Mapped[str] = mapped_column(
        ForeignKey("albums.uri", ondelete="CASCADE"),
        nullable=False
    )
    duration_ms: Mapped[int] = mapped_column(nullable=True)
    added_by: Mapped[str] = mapped_column(nullable=False)
    mime: Mapped[str] = mapped_column(nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=datetime.now(timezone.utc),
        nullable=False,
    )
    external_uri: Mapped[str] = mapped_column(nullable=True)


class RecordArtistAssociation(Base):
    __tablename__ = "record_artist_association"

    record_uri: Mapped[str] = mapped_column(
        ForeignKey("music_repository.uri", ondelete="CASCADE"),
        primary_key=True
    )
    artist_uri: Mapped[str] = mapped_column(
        ForeignKey("artists.uri", ondelete="CASCADE"),
        primary_key=True
    )


class AlbumArtistAssociation(Base):
    __tablename__ = "artist_album_association"

    album_uri: Mapped[str] = mapped_column(
        ForeignKey("albums.uri", ondelete="CASCADE"),
        primary_key=True
    )
    artist_uri: Mapped[str] = mapped_column(
        ForeignKey("artists.uri", ondelete="CASCADE"),
        primary_key=True
    )


class Users(Base):
    __tablename__ = "users"
    uri: Mapped[str] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(nullable=False)
    password: Mapped[bytes] = mapped_column(nullable=False)
    access_level: Mapped[int] = mapped_column(nullable=False)


class MusiqlHistory(Base):
    __tablename__ = "music_history"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    uri: Mapped[str] = mapped_column(
        ForeignKey("music_repository.uri", ondelete="CASCADE"),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.uri", ondelete="CASCADE"),
        nullable=True
    )
    duration_played: Mapped[float] = mapped_column(nullable=False)
    listened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserLirbary(Base):
    __tablename__ = "libraries"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.uri", ondelete="CASCADE"),
        primary_key=True
    )
    record_id: Mapped[str] = mapped_column(
        ForeignKey("music_repository.uri", ondelete="CASCADE"),
        primary_key=True
    )


class Models(Base):
    __tablename__ = "models"
    uri: Mapped[str] = mapped_column(nullable=False, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.uri", ondelete="CASCADE"),
        nullable=False
    )
    model_name: Mapped[str] = mapped_column(nullable=True)
    algorithm: Mapped[str] = mapped_column(nullable=True)


class ModelUpdates(Base):
    __tablename__ = "model_updates"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    dttm: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

class UploadJobs(Base):
    __tablename__ = "upload_jobs"
    uri: Mapped[str] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(nullable=False)
    source_type: Mapped[str] = mapped_column(nullable=False)
    source_id: Mapped[str] = mapped_column(nullable=False)
    subtasks: Mapped[int] = mapped_column(nullable=False)
    progress: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False)
    requestor: Mapped[str] = mapped_column(nullable=False, index=True)
    dttm: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=True)
    association: Mapped[str] = mapped_column(nullable=True)