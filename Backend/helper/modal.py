from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

# ---------------------------
# Quality Detail Schema
# ---------------------------
class QualityDetail(BaseModel):
    quality: str
    id: str
    name: str
    size: str


# ---------------------------
# Subtitle Detail Schema (NEW)
# ---------------------------
class SubtitleDetail(BaseModel):
    id: str  # Encoded string with chat_id and msg_id
    name: str  # Subtitle filename
    size: str  # Human-readable size
    language: Optional[str] = None  # Detected language code (e.g., "en", "es")
    format: Optional[str] = None  # Subtitle format (e.g., "srt", "ass")


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
    updated_on: datetime = Field(default_factory=datetime.utcnow)
    telegram: Optional[List[QualityDetail]]
    subtitles: Optional[List[SubtitleDetail]] = Field(default_factory=list)