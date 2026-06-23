from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# ---------------------------
# Quality Detail Schema
# ---------------------------
class QualityDetail(BaseModel):
    quality: str
    id: str
    name: str
    size: str
    # Original Telegram upload/add date. Used to keep latest movie/series order stable after rescan.
    date_added: Optional[datetime] = None
    # Optional fields used by split ZIP streams (name.zip.001, name.zip.002, ...).
    source_type: Optional[str] = "telegram"
    archive_name: Optional[str] = None
    part_count: Optional[int] = None
    parts: Optional[List[Dict[str, Any]]] = None
    # Optional source/topic metadata for admin topic stats and UI labels.
    source_chat_id: Optional[int] = None
    source_topic_id: Optional[int] = None
    source_link: Optional[str] = None
    release_group: Optional[str] = None


# ---------------------------
# Subtitle Detail Schema
# ---------------------------
class SubtitleDetail(BaseModel):
    id: str            # encoded hash (chat_id + msg_id)
    language: str      # ISO 639-1 code: "en", "si", "fr", etc.
    name: str          # original filename
    format: str        # "srt", "vtt", "ass", "sub"
    date_added: Optional[datetime] = None
    source_chat_id: Optional[int] = None
    source_topic_id: Optional[int] = None
    source_link: Optional[str] = None


# ---------------------------
# Episode Schema
# ---------------------------
class Episode(BaseModel):
    episode_number: int
    title: str
    episode_backdrop: Optional[str] = None
    overview: Optional[str] = None
    released: Optional[str] = None
    telegram: Optional[List[QualityDetail]]
    subtitles: Optional[List[SubtitleDetail]] = Field(default_factory=list)


# ---------------------------
# Season Schema
# ---------------------------
class Season(BaseModel):
    season_number: int
    episodes: List[Episode] = Field(default_factory=list)


# ---------------------------
# TV Show Schema
# ---------------------------
class TVShowSchema(BaseModel):
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    db_index: int
    title: str
    genres: Optional[List[str]] = None
    description: Optional[str] = None
    rating: Optional[float] = None
    release_year: Optional[int] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    logo: Optional[str] = None
    cast: Optional[List[str]] = None
    runtime: Optional[str] = None
    media_type: str
    # added_on / updated_on are upload-order timestamps, not rescan time.
    added_on: datetime = Field(default_factory=datetime.utcnow)
    updated_on: datetime = Field(default_factory=datetime.utcnow)
    seasons: List[Season] = Field(default_factory=list)


# ---------------------------
# Movie Schema
# ---------------------------
class MovieSchema(BaseModel):
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    db_index: int
    title: str
    genres: Optional[List[str]] = None
    description: Optional[str] = None
    rating: Optional[float] = None
    release_year: Optional[int] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    logo: Optional[str] = None
    cast: Optional[List[str]] = None
    runtime: Optional[str] = None
    media_type: str
    # added_on / updated_on are upload-order timestamps, not rescan time.
    added_on: datetime = Field(default_factory=datetime.utcnow)
    updated_on: datetime = Field(default_factory=datetime.utcnow)
    telegram: Optional[List[QualityDetail]]
    subtitles: Optional[List[SubtitleDetail]] = Field(default_factory=list)
