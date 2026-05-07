import dataclasses
from typing import Any, Optional


@dataclasses.dataclass
class spotify_user:
    external_urls: dict
    href: str
    id: str
    type: str
    uri: str
    display_name: str

    @classmethod
    def create_from_dict(cls, user):
        return cls(**user)


@dataclasses.dataclass
class spotify_playlist:
    collaborative: bool
    description: str
    external_urls: dict
    href: str
    id: str
    images: list
    name: str
    owner: spotify_user
    public: bool
    snapshot_id: str
    items: dict
    tracks: dict
    type: str
    uri: str
    primary_color: Any

    @classmethod
    def create_from_dict(cls, playlist: dict):
        owner: spotify_user = spotify_user.create_from_dict(playlist.get("owner", {}))
        return cls(
            collaborative=playlist.get("collaborative"),
            description=playlist.get("description"),
            external_urls=playlist.get("external_urls"),
            href=playlist.get("href"),
            id=playlist.get("id"),
            images=playlist.get("images"),
            name=playlist.get("name"),
            owner=owner,
            public=playlist.get("public"),
            snapshot_id=playlist.get("snapshot_id"),
            items=playlist.get("items"),
            tracks=playlist.get("tracks"),
            type=playlist.get("type"),
            uri=playlist.get("uri"),
            primary_color=playlist.get("primary_color"),
        )


@dataclasses.dataclass
class spotify_artist:
    external_urls: dict
    href: str
    id: str
    name: str
    type: str
    uri: str

    @classmethod
    def create_from_dict(cls, artist: dict):
        return cls(**artist)


@dataclasses.dataclass
class spotify_album:
    type: str
    album_type: str
    href: str
    id: str
    images: list
    name: str
    release_date: str
    release_date_precision: str
    uri: str
    artists: list[spotify_artist]
    external_urls: dict
    total_tracks: int
    ...
    is_playable: bool = True
    tracks: Optional[dict] = None
    copyrights: Optional[list] = None
    external_ids: Optional[dict] = None
    genres: Optional[list] = None

    @classmethod
    def create_from_dict(cls, album: dict):
        return cls(**album)


@dataclasses.dataclass
class spotify_item:
    is_playable: bool
    explicit: bool
    type: str
    episode: bool
    track: bool
    album: spotify_album
    artists: list[spotify_artist]
    disc_number: int
    track_number: int
    duration_ms: int
    external_ids: dict
    external_urls: dict
    href: str
    id: str
    name: str
    uri: str
    is_local: bool

    @classmethod
    def create_from_dict(cls, item: dict):
        album = spotify_album.create_from_dict(item["album"])

        artists = [spotify_artist.create_from_dict(a) for a in item.get("artists", [])]

        return cls(
            is_playable=item.get("is_playable"),
            explicit=item.get("explicit"),
            type=item.get("type"),
            episode=item.get("episode"),
            track=item.get("track"),
            album=album,
            artists=artists,
            disc_number=item.get("disc_number"),
            track_number=item.get("track_number"),
            duration_ms=item.get("duration_ms"),
            external_ids=item.get("external_ids"),
            external_urls=item.get("external_urls"),
            href=item.get("href"),
            id=item.get("id"),
            name=item.get("name"),
            uri=item.get("uri"),
            is_local=item.get("is_local"),
        )
