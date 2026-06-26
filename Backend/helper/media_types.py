"""Canonical media-type handling for metadata, database and WebUI routes.

External providers do not always use the two storage categories used by this
project.  IMDb/Cinemeta may return values such as ``tvMovie`` and
``tvMiniSeries``.  The database intentionally stores only ``movie`` and
``tv``; this module maps every supported alias to one of those two values.
"""

from __future__ import annotations

import re
from typing import Any

# Keys are compacted with _type_key(), so casing, spaces, underscores and
# hyphens are interchangeable: tvMini, tv-mini and tv_mini all work.
_MOVIE_TYPE_KEYS = {
    "movie",
    "movies",
    "film",
    "tvmovie",
    "televisionmovie",
}

_TV_TYPE_KEYS = {
    "tv",
    "series",
    "tvseries",
    "tvmini",
    "tvminiseries",
    "miniseries",
    "limitedseries",
}

# Used by FastAPI query validation.  The API normalizes accepted values before
# any database operation, so aliases never create separate collections.
MEDIA_TYPE_QUERY_PATTERN = (
    r"(?i)^(?:movie|movies|film|tvmovie|televisionmovie|"
    r"tv|series|tvseries|tvmini|tvminiseries|miniseries|limitedseries|"
    r"tv[ _-]?movie|tv[ _-]?series|tv[ _-]?mini(?:[ _-]?series)?|"
    r"mini[ _-]?series|limited[ _-]?series)$"
)


def _type_key(value: Any) -> str:
    """Return a comparison key for a provider or WebUI media-type value."""
    return re.sub(r"[\s_-]+", "", str(value or "").casefold())


def canonical_media_type(value: Any, default: str = "movie") -> str:
    """Map a provider-specific media type to the internal ``movie``/``tv``.

    ``tvmovie`` is a movie and ``tvmini``/``tvMiniSeries`` are series.  Unknown
    values use the supplied default to preserve the project's historical
    behavior of treating non-series values as movies.
    """
    key = _type_key(value)
    if key in _TV_TYPE_KEYS:
        return "tv"
    if key in _MOVIE_TYPE_KEYS:
        return "movie"
    return "tv" if _type_key(default) in _TV_TYPE_KEYS else "movie"


def is_tv_type(value: Any) -> bool:
    return canonical_media_type(value) == "tv"


def stremio_media_type(value: Any) -> str:
    """Return Stremio's public media type for an internal/provider value."""
    return "series" if is_tv_type(value) else "movie"


def cinemeta_media_type(value: Any) -> str:
    """Return the Cinemeta endpoint segment for an internal/provider value."""
    return "series" if is_tv_type(value) else "movie"
