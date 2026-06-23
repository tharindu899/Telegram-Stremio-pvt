from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from urllib.parse import unquote
from Backend.config import Telegram
from Backend import db, __version__
import PTN
import re
from datetime import datetime, timezone, timedelta
from Backend.fastapi.security.tokens import verify_token


# --- Configuration ---
BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Telegram"
ADDON_VERSION = __version__
PAGE_SIZE = 15

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])

# Define available genres
GENRES = [
    "Action", "Adventure", "Animation", "Biography", "Comedy",
    "Crime", "Documentary", "Drama", "Family", "Fantasy",
    "History", "Horror", "Music", "Mystery", "Romance",
    "Sci-Fi", "Sport", "Thriller", "War", "Western"
]


def format_released_date(media):
    year = media.get("release_year")
    if year:
        try:
            return datetime(int(year), 1, 1).isoformat() + "Z"
        except:
            return None

    return None

# --- Helper Functions ---
def convert_to_stremio_meta(item: dict) -> dict:
    media_type = "series" if item.get("media_type") == "tv" else "movie"
    
    meta = {
        "id": item.get('imdb_id'),
        "type": media_type,
        "name": item.get("title"),
        "poster": item.get("poster") or "",
        "logo": item.get("logo") or "",
        "year": item.get("release_year"),
        "releaseInfo": str(item.get("release_year", "")),
        "imdb_id": item.get("imdb_id", ""),
        "moviedb_id": item.get("tmdb_id", ""),
        "background": item.get("backdrop") or "",
        "genres": item.get("genres") or [],
        "imdbRating": str(item.get("rating") or ""),
        "description": item.get("description") or "",
        "cast": item.get("cast") or [],
        "runtime": item.get("runtime") or "",
    }

    return meta


# Stream cards in Stremio are rendered by the client, so this addon sends
# clean, human-readable stream names plus plain release metadata. Nuvio's
# badge profile can recognise the normal words (for example, ``2160p``,
# ``WEB-DL``, ``Hindi`` and ``DD+``) without cluttering the stream card with
# extra square-bracket tags.

_LANGUAGE_TAGS = (
    ("HIN", r"\b(?:hindi|hin)\b"),
    ("TEL", r"\b(?:telugu|tel)\b"),
    ("TAM", r"\b(?:tamil|tam)\b"),
    ("MAL", r"\b(?:malayalam|mal)\b"),
    ("KAN", r"\b(?:kannada|kan)\b"),
    ("BEN", r"\b(?:bengali|bangla|ben)\b"),
    ("SIN", r"\b(?:sinhala|sin)\b"),
    ("ENG", r"\b(?:english|eng)\b"),
    ("KOR", r"\b(?:korean|kor)\b"),
    ("JPN", r"\b(?:japanese|jpn)\b"),
    ("CHI", r"\b(?:chinese|mandarin|chi)\b"),
    ("ARA", r"\b(?:arabic|ara)\b"),
    ("SPA", r"\b(?:spanish|spa)\b"),
    ("FRE", r"\b(?:french|fre|fra)\b"),
    ("GER", r"\b(?:german|ger|deutsch)\b"),
    ("RUS", r"\b(?:russian|rus)\b"),
)


def _stream_tag_text(filename: str, quality: str = "") -> str:
    """Normalize a release name without destroying useful metadata tokens."""
    raw = f"{filename or ''} {quality or ''}".lower()
    raw = re.sub(r"%[0-9a-f]{2}", " ", raw)
    return re.sub(r"[_\-\[\](){}]+", " ", raw)


def _safe_ptn_parse(filename: str) -> dict:
    try:
        parsed = PTN.parse(filename or "")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        # A malformed Telegram filename must still receive useful text tags.
        return {}


def _find_resolution_tag(text: str, parsed: dict, fallback: str) -> str:
    resolution = str(parsed.get("resolution") or fallback or "").lower()
    combined = f"{text} {resolution}"
    if re.search(r"\b(?:2160p|4k|uhd)\b", combined):
        return "4K"
    if re.search(r"\b1440p\b", combined):
        return "1440P"
    if re.search(r"\b(?:1080p|fhd)\b", combined):
        return "1080P"
    if re.search(r"\b(?:720p|hd)\b", combined):
        return "720P"
    if re.search(r"\b480p\b", combined):
        return "480P"
    if re.search(r"\b360p\b", combined):
        return "360P"
    return ""


def _find_source_tag(text: str, parsed: dict) -> str:
    parsed_quality = str(parsed.get("quality") or "").lower()
    combined = f"{text} {parsed_quality}"
    if re.search(r"\b(?:remux|bdremux)\b", combined):
        return "REMUX"
    if re.search(r"\b(?:web[\s.-]*dl|webdl)\b", combined):
        return "WebDL"
    if re.search(r"\b(?:web[\s.-]*rip|webrip)\b", combined):
        return "WebRip"
    if re.search(r"\b(?:blu[\s.-]*ray|bluray|bdrip|brrip)\b", combined):
        return "BluRay"
    if re.search(r"\b(?:hdtv|hdtvrip)\b", combined):
        return "HDTV"
    if re.search(r"\bhdrip\b", combined):
        return "HDRip"
    if re.search(r"\b(?:dvd[\s.-]*rip|dvdrip)\b", combined):
        return "DVDRip"
    if re.search(r"\b(?:tele[\s.-]*sync|telesync|hdts|camrip|cam)\b", combined):
        return "CAM"
    return ""


def _find_language_tags(text: str) -> list[str]:
    tags: list[str] = []
    for tag, pattern in _LANGUAGE_TAGS:
        if re.search(pattern, text) and tag not in tags:
            tags.append(tag)

    # A release labelled Multi Audio can be recognised even if it omits names.
    if not tags and re.search(r"\b(?:multi(?:[\s.-]*(?:audio|lang))?|multiaudio)\b", text):
        return ["MULTI"]

    # Keep mobile stream names readable. The complete release name remains in
    # the title, while the first two language badges cover the usual dual-audio
    # releases such as Hindi + Telugu.
    return tags[:2]


def _find_codec_tags(text: str, parsed: dict) -> list[str]:
    tags: list[str] = []
    codec = str(parsed.get("codec") or "").lower()
    combined = f"{text} {codec}"

    if re.search(r"\b(?:hevc|x265|h[ .-]?265)\b", combined):
        tags.append("HEVC")
    elif re.search(r"\b(?:avc|x264|h[ .-]?264)\b", combined):
        tags.append("AVC")
    elif re.search(r"\bav1\b", combined):
        tags.append("AV1")
    elif re.search(r"\bvp9\b", combined):
        tags.append("VP9")

    # Dolby Vision and HDR10+ may be present together, so these are not
    # mutually exclusive.
    if re.search(r"\b(?:dolby[\s.-]*vision|dovi|dv)\b", combined):
        tags.append("DV")
    if re.search(r"\bhdr10\+(?!\w)", combined):
        tags.append("HDR10+")
    elif re.search(r"\bhdr10\b", combined):
        tags.append("HDR10")
    elif re.search(r"\bhdr\b", combined):
        tags.append("HDR")

    return tags


def _find_audio_tags(text: str, parsed: dict) -> list[str]:
    tags: list[str] = []
    audio = str(parsed.get("audio") or "").lower()
    combined = f"{text} {audio}"

    if re.search(r"\b(?:truehd|true[ .-]*hd)\b", combined):
        tags.append("TrueHD")
    elif re.search(r"(?:\bddp(?=\d|\b)|\bdd\+(?=\d|\b)|\beac3\b|\be[\s.-]*ac[\s.-]*3\b)", combined):
        tags.append("DD+")
    elif re.search(r"\b(?:dts[\s.-]*x|dtsx)\b", combined):
        tags.append("DTS:X")
    elif re.search(r"\bdts\b", combined):
        tags.append("DTS")
    elif re.search(r"\b(?:dolby[\s.-]*digital|ac3)\b", combined):
        tags.append("DD")
    elif re.search(r"\baac\b", combined):
        tags.append("AAC")
    elif re.search(r"\bopus\b", combined):
        tags.append("OPUS")

    if re.search(r"\b(?:atmos|dolby[\s.-]*atmos)\b", combined):
        tags.append("ATMOS")

    channel_match = re.search(r"(?<!\d)([257]\.[01])(?!\d)", combined)
    if channel_match:
        tags.append(channel_match.group(1))

    return tags


_RESOLUTION_NAME_LABELS = {
    "4K": "2160p",
    "1440P": "1440p",
    "1080P": "1080p",
    "720P": "720p",
    "480P": "480p",
    "360P": "360p",
}

_SOURCE_NAME_LABELS = {
    "WebDL": "WEB-DL",
    "WebRip": "WEBRip",
    "BluRay": "BluRay",
    "REMUX": "REMUX",
    "HDTV": "HDTV",
    "HDRip": "HDRip",
    "DVDRip": "DVDRip",
    "CAM": "CAM",
}

_LANGUAGE_DISPLAY_LABELS = {
    "HIN": "Hindi",
    "TEL": "Telugu",
    "TAM": "Tamil",
    "MAL": "Malayalam",
    "KAN": "Kannada",
    "BEN": "Bengali",
    "SIN": "Sinhala",
    "ENG": "English",
    "KOR": "Korean",
    "JPN": "Japanese",
    "CHI": "Chinese",
    "ARA": "Arabic",
    "SPA": "Spanish",
    "FRE": "French",
    "GER": "German",
    "RUS": "Russian",
    "MULTI": "Multi Audio",
}


def _clean_quality_label(quality: str) -> str:
    """Create a safe fallback label when a filename has no recognised tags."""
    label = re.sub(r"[\[\](){}]+", " ", str(quality or ""))
    label = re.sub(r"[_\-]+", " ", label)
    return re.sub(r"\s+", " ", label).strip()


def _stream_name_label(resolution: str, source: str, quality: str) -> str:
    """Return a short card heading such as ``Telegram 2160p WEB-DL``."""
    parts = ["Telegram"]
    if resolution:
        parts.append(_RESOLUTION_NAME_LABELS.get(resolution, resolution))
    if source:
        parts.append(_SOURCE_NAME_LABELS.get(source, source))

    # Sparse Telegram uploads may not contain a standard resolution/source.
    # In that case retain a small cleaned quality label rather than emitting
    # bracket tags or an empty heading.
    if len(parts) == 1:
        fallback = _clean_quality_label(quality)
        if fallback:
            parts.append(fallback)
    return " ".join(parts)


def _format_metadata_line(prefix: str, values: list[str]) -> str:
    compact = [value for value in values if value]
    return f"{prefix} {' · '.join(compact)}" if compact else ""


def format_stream_details(filename: str, quality: str, size: str) -> tuple[str, str]:
    """Build a clean stream heading and normal metadata for badge-aware clients.

    Example result:

    ``Telegram 2160p WEB-DL``
    ``📁 Movie.2160p.WEB-DL.Hindi.HEVC.DDP5.1.mkv``
    ``🗣️ Hindi``
    ``💾 16.3 GB``

    Technical rows such as ``🎞️ WEB-DL · HEVC`` and ``🎧 DD+ · 5.1`` are
    deliberately omitted. Nuvio reads the filename and behavior hints, then
    shows that metadata as compact icon badges instead.
    """
    parsed = _safe_ptn_parse(filename)
    text = _stream_tag_text(filename, quality)

    resolution = _find_resolution_tag(text, parsed, quality)
    source = _find_source_tag(text, parsed)
    languages = _find_language_tags(text)
    stream_name = _stream_name_label(resolution, source, quality)
    title_parts = [f"📁 {filename}"]

    # Keep the card body minimal. Nuvio's profile matches resolution, source,
    # codec and audio directly from the filename / behavior hints and renders
    # them as image badges, rather than repeating two technical text rows.
    language_line = _format_metadata_line(
        "🗣️",
        [_LANGUAGE_DISPLAY_LABELS.get(tag, tag) for tag in languages],
    )
    if language_line:
        title_parts.append(language_line)

    if size:
        title_parts.append(f"💾 {size}")

    return stream_name, "\n".join(title_parts)


def get_resolution_priority(stream_name: str) -> int:
    resolution_map = {
        "2160p": 2160, "4k": 2160, "uhd": 2160,
        "1080p": 1080, "fhd": 1080,
        "720p": 720, "hd": 720,
        "480p": 480, "sd": 480,
        "360p": 360,
    }
    for res_key, res_value in resolution_map.items():
        if res_key in stream_name.lower():
            return res_value
    return 1


def _parse_size_to_mb(size_text: str) -> float:
    text = str(size_text or "").lower().replace(",", "")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(tb|gb|mb|kb)", text)
    if not m:
        return 10**9
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "tb": return val * 1024 * 1024
    if unit == "gb": return val * 1024
    if unit == "mb": return val
    if unit == "kb": return val / 1024
    return val


def _parse_size_to_bytes(size_text: str) -> Optional[int]:
    """Convert a human-readable release size (for example, ``2.53GB``) to bytes.

    Nuvio's native size-chip feature reads the standard Stremio
    ``behaviorHints.videoSize`` field, which must be a byte count.
    """
    text = str(size_text or "").lower().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(tb|gb|mb|kb|b)\b", text)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)
    multipliers = {
        "tb": 1024 ** 4,
        "gb": 1024 ** 3,
        "mb": 1024 ** 2,
        "kb": 1024,
        "b": 1,
    }
    byte_count = int(value * multipliers[unit])
    return byte_count if byte_count > 0 else None


def _stream_behavior_hints(filename: str, size_text: str) -> dict:
    """Build client-readable release metadata without changing playback URLs."""
    hints: dict = {}
    if filename:
        hints["filename"] = filename
    video_size = _parse_size_to_bytes(size_text)
    if video_size is not None:
        hints["videoSize"] = video_size
    return hints


def _source_mode_label(quality: dict) -> tuple[str, str, int]:
    src = (quality.get("source_type") or "telegram").lower()
    name = (quality.get("name") or "").lower()
    if src == "split_zip" or "[split zip" in name:
        return "Split ZIP", "", 0
    if src == "split_file" or "[split x" in name:
        return "Split Video", "", 1
    return "Direct", "", 2


def _stream_sort_key_from_quality(quality: dict) -> tuple:
    mode, _hint, mode_score = _source_mode_label(quality)
    res = get_resolution_priority(str(quality.get("quality") or quality.get("name") or ""))
    # Prefer higher resolution, then direct/split-video, then smaller size for same release family.
    return (res, mode_score, -_parse_size_to_mb(quality.get("size")))

# --- Routes ---
@router.get("/{token}/manifest.json")
async def get_manifest(token: str, token_data: dict = Depends(verify_token)):
    _sub_resource = {
        "name": "subtitles",
        "types": ["movie", "series"],
        "idPrefixes": ["tt"]
    }

    if Telegram.HIDE_CATALOG:
        resources = ["stream", _sub_resource]
        catalogs = []
    else:
        resources = ["catalog", "meta", "stream", _sub_resource]
        catalogs = [
            {
                "type": "movie",
                "id": "latest_movies",
                "name": "Latest",
                "extra": [
                    {"name": "genre", "isRequired": False, "options": GENRES},
                    {"name": "skip"}
                ],
                "extraSupported": ["genre", "skip"]
            },
            {
                "type": "movie",
                "id": "top_movies",
                "name": "Popular",
                "extra": [
                    {"name": "genre", "isRequired": False, "options": GENRES},
                    {"name": "skip"},
                    {"name": "search", "isRequired": False}
                ],
                "extraSupported": ["genre", "skip", "search"]
            },
            {
                "type": "series",
                "id": "latest_series",
                "name": "Latest",
                "extra": [
                    {"name": "genre", "isRequired": False, "options": GENRES},
                    {"name": "skip"}
                ],
                "extraSupported": ["genre", "skip"]
            },
            {
                "type": "series",
                "id": "top_series",
                "name": "Popular",
                "extra": [
                    {"name": "genre", "isRequired": False, "options": GENRES},
                    {"name": "skip"},
                    {"name": "search", "isRequired": False}
                ],
                "extraSupported": ["genre", "skip", "search"]
            }
        ]

        # Add visible custom catalogs to the Stremio home screen.
        # Each custom catalog is exposed once for movies and once for series because
        # Stremio catalogs are type-specific. Hidden catalogs remain manageable in the
        # web panel, but are not included in the manifest.
        try:
            custom_catalogs = await db.get_custom_catalogs(visible_only=True)
            for catalog in custom_catalogs:
                catalog_id = str(catalog.get("_id"))
                catalog_name = catalog.get("name") or "Custom Catalog"
                catalogs.append({
                    "type": "movie",
                    "id": f"custom_{catalog_id}",
                    "name": catalog_name,
                    "extra": [{"name": "skip"}],
                    "extraSupported": ["skip"],
                })
                catalogs.append({
                    "type": "series",
                    "id": f"custom_{catalog_id}",
                    "name": catalog_name,
                    "extra": [{"name": "skip"}],
                    "extraSupported": ["skip"],
                })
        except Exception:
            pass


    # Build dynamic name/description/version with subscription info
    addon_name = ADDON_NAME
    addon_desc = "Streams movies and series from your Telegram."
    addon_version = ADDON_VERSION
    expiry_obj = None

    if Telegram.SUBSCRIPTION:
        user_id = token_data.get("user_id")
        if user_id:
            from Backend import db as _db
            try:
                user = await _db.get_user(int(user_id))
                if user and user.get("subscription_status") == "active":
                    expiry_obj = user.get("subscription_expiry")
                    if expiry_obj:
                        expiry_str = expiry_obj.strftime("%d %b %Y").lstrip("0")
                        addon_name = f"{ADDON_NAME} — Expires {expiry_str}"
                        addon_desc = (
                            f"📅 Subscription active until {expiry_str}.\n"
                            f"Streams movies and series from your Telegram."
                        )
                        # Encode expiry epoch (low 16 bits, hex) into version so
                        # Stremio detects a change when subscription is updated.
                        epoch_tag = format(int(expiry_obj.timestamp()) & 0xFFFF, "x")
                        addon_version = f"{ADDON_VERSION}-{epoch_tag}"
                    else:
                        addon_name = f"{ADDON_NAME} — Active"
                        addon_desc = "✅ Subscription active.\nStreams movies and series from your Telegram."
            except Exception:
                pass  # Fallback to defaults on error

    # Configure URL — opening this reinstalls the addon with latest manifest
    configure_url = f"{Telegram.BASE_URL}/stremio/{token}/configure"

    return {
        "id": f"telegram.media.{token[:8]}",   # per-user ID so each token is independent
        "version": addon_version,
        "name": addon_name,
        "logo": "https://i.postimg.cc/XqWnmDXr/Picsart-25-10-09-08-09-45-867.png",
        "description": addon_desc,
        "types": ["movie", "series"],
        "resources": resources,
        "catalogs": catalogs,
        "idPrefixes": ["tt"],
        "behaviorHints": {
            "configurable": True,
            "configurationRequired": False
        },
        "config": [
            {
                "key": "manifest_url",
                "title": "Your Addon URL (copy to reinstall)",
                "type": "text",
                "default": f"{Telegram.BASE_URL}/stremio/{token}/manifest.json"
            }
        ]
    }


@router.get("/{token}/configure")
async def configure_addon(token: str):
    """
    Configure/update page for the Stremio addon.
    Uses the correct stremio://addon_install?manifest= deep-link so Stremio
    actually shows the Install/Update dialog when the button is clicked.
    """
    from urllib.parse import quote
    from fastapi.responses import HTMLResponse
    from Backend import db as _db

    manifest_url = f"{Telegram.BASE_URL}/stremio/{token}/manifest.json"
    # Universal Stremio web install — works on desktop and mobile
    web_install_url = f"https://web.stremio.com/#/?addon_manifest={quote(manifest_url, safe='')}"

    # Fetch token/user info for display.
    # Important: the configure page is only an install helper. It should not show
    # a scary "Unknown" state when the token itself is valid but not linked to a
    # Telegram user yet, or when subscription mode is disabled.
    token_doc = await _db.get_api_token(token)
    token_valid = bool(token_doc)
    user_name = "Invalid token"
    expiry_str = "N/A"
    status_color = "#ef4444"
    status_text = "Invalid"
    helper_note = "This token was not found in the database. Create/copy a fresh token from the admin panel."

    if token_doc:
        uid = token_doc.get("user_id")
        user_name = token_doc.get("name") or (f"User {uid}" if uid else "Unlinked token")
        helper_note = "This token is valid. You can install the addon with the manifest URL below."

        if not Telegram.SUBSCRIPTION:
            status_color = "#22c55e"
            status_text = "Active"
            expiry_str = "Unlimited"
        elif not uid:
            status_color = "#f59e0b"
            status_text = "Needs user link"
            helper_note = "Token is valid, but subscription mode is enabled. Link a Telegram user ID and assign a plan in Admin → Access."
        else:
            try:
                user = await _db.get_user(int(uid))
                if user:
                    user_name = user.get("first_name") or user.get("username") or token_doc.get("name") or f"User {uid}"
                    sub_status = user.get("subscription_status", "")
                    expiry = user.get("subscription_expiry")
                    if expiry:
                        expiry_str = expiry.strftime("%d %b %Y").lstrip("0")
                    if sub_status == "active":
                        status_color = "#22c55e"
                        status_text = "Active"
                        helper_note = "Subscription is active. Install or update the addon below."
                    else:
                        status_color = "#ef4444"
                        status_text = "No active plan"
                        helper_note = "This token is linked, but the user has no active subscription plan."
                else:
                    status_color = "#f59e0b"
                    status_text = "Needs plan"
                    helper_note = "Token is linked, but this Telegram user does not have a subscription record. Assign a plan in Admin → Access."
            except Exception:
                status_color = "#f59e0b"
                status_text = "Check user"
                helper_note = "Could not read the linked user. Check the Telegram user ID in Admin → Access."

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Update Telegram Stremio Addon</title>
  <link rel="icon" type="image/svg+xml" href="/static/icons/favicon.svg">
  <link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">
  <link rel="manifest" href="/static/site.webmanifest">
  <meta name="theme-color" content="#0f0f1a">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0f0f1a; color: #e2e8f0;
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
      padding: 24px;
    }}
    .card {{
      background: #1e1e2e; border: 1px solid #2d2d44; border-radius: 9px;
      padding: 28px 24px; max-width: 480px; width: 100%; text-align: center;
    }}
    .logo {{ width: 58px; height: 58px; border-radius: 14px; margin: 0 auto 14px; display:flex; align-items:center; justify-content:center; background:#7c3aed22; color:#a78bfa; border:1px solid #7c3aed44; font-size: 28px; }}
    h1 {{ font-size: 1.5rem; font-weight: 700; color: #f8fafc; margin-bottom: 6px; }}
    .sub-title {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 28px; }}
    .info-row {{
      display: flex; justify-content: space-between; align-items: center;
      background: #2a2a3e; border-radius: 9px; padding: 12px 16px;
      margin-bottom: 12px; font-size: 0.9rem;
    }}
    .info-label {{ color: #94a3b8; }}
    .info-val {{ font-weight: 600; color: #f1f5f9; }}
    .status-badge {{
      display: inline-block; padding: 2px 10px; border-radius: 999px;
      font-size: 0.8rem; font-weight: 700;
      background: {status_color}22; color: {status_color};
    }}
    .btn-update {{
      display: block; width: 100%;
      background: linear-gradient(135deg, #7c3aed, #4f46e5);
      color: white; font-weight: 700; font-size: 1rem;
      padding: 12px 18px; border-radius: 9px; border: none;
      cursor: pointer; text-decoration: none; margin: 20px 0 10px;
      transition: opacity 0.2s;
    }}
    .btn-update:hover {{ opacity: 0.85; }}
    .btn-web {{
      display: block; color: #6366f1; font-size: 0.85rem;
      text-decoration: underline; margin-bottom: 20px;
    }}
    .steps {{
      background: #2a2a3e; border-radius: 9px; padding: 14px 18px;
      margin: 16px 0; text-align: left; font-size: 0.85rem; color: #cbd5e1;
    }}
    .steps b {{ color: #f1f5f9; }}
    .steps ol {{ margin-top: 8px; margin-left: 18px; line-height: 1.8; }}
    .url-box {{
      background: #111827; border: 1px solid #374151; border-radius: 8px;
      padding: 10px 14px; font-family: monospace; font-size: 0.75rem;
      color: #94a3b8; word-break: break-all; text-align: left; margin-top: 16px;
    }}
    .btn-copy {{
      margin-top: 10px; width: 100%; padding: 10px;
      background: #1e293b; border: 1px solid #374151; color: #94a3b8;
      border-radius: 8px; cursor: pointer; font-size: 0.85rem; transition: all 0.2s;
    }}
    .btn-copy:hover {{ background: #334155; color: #f1f5f9; }}
    .hint {{ color: #64748b; font-size: 0.78rem; margin-top: 6px; }}
    .hint-card {{
      background: #111827; border: 1px solid #374151; border-radius: 9px;
      padding: 10px 12px; color: #cbd5e1; font-size: 0.82rem; line-height: 1.45;
      margin: 0 0 18px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo"><i class="fa-solid fa-clapperboard"></i></div>
    <h1>Telegram Stremio Addon</h1>
    <p class="sub-title">Click the button below to install or update your addon in Stremio.</p>
    <div class="hint-card">{helper_note}</div>

    <div class="info-row">
      <span class="info-label">User</span>
      <span class="info-val">{user_name}</span>
    </div>
    <div class="info-row">
      <span class="info-label">Status</span>
      <span class="status-badge">{status_text}</span>
    </div>
    <div class="info-row">
      <span class="info-label">Expires</span>
      <span class="info-val">{expiry_str}</span>
    </div>

    <a href="{web_install_url}" class="btn-update" target="_blank">
      <i class="fa-solid fa-bolt"></i> Install / Update in Stremio
    </a>

    <div class="steps">
      <b>Or install manually:</b>
      <ol>
        <li>Open Stremio → <b>Add-ons</b> tab</li>
        <li>Click the <b><i class="fa-solid fa-magnifying-glass"></i> Search / URL</b> icon</li>
        <li>Paste the URL below and press Enter</li>
      </ol>
    </div>

    <div class="url-box" id="murl">{manifest_url}</div>
    <button onclick="copyUrl()" class="btn-copy"><i class="fa-solid fa-copy"></i> Copy URL</button>
    <script>
      function copyUrl() {{
        navigator.clipboard.writeText('{manifest_url}').then(() => {{
          const b = document.querySelector('.btn-copy');
          b.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
          setTimeout(() => b.innerHTML = '<i class="fa-solid fa-copy"></i> Copy URL', 2000);
        }});
      }}
    </script>
  </div>
</body>
</html>"""
    return HTMLResponse(html)




@router.get("/{token}/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/{token}/catalog/{media_type}/{id}.json")
async def get_catalog(token: str, media_type: str, id: str, extra: Optional[str] = None, token_data: dict = Depends(verify_token)):
    if Telegram.HIDE_CATALOG:
        raise HTTPException(status_code=404, detail="Catalog disabled")

    if media_type not in ["movie", "series"]:
        raise HTTPException(status_code=404, detail="Invalid catalog type")

    genre_filter = None
    search_query = None
    stremio_skip = 0

    if extra:
        params = extra.replace("&", "/").split("/")
        for param in params:
            if param.startswith("genre="):
                genre_filter = unquote(param.removeprefix("genre="))
            elif param.startswith("search="):
                search_query = unquote(param.removeprefix("search="))
            elif param.startswith("skip="):
                try:
                    stremio_skip = int(param.removeprefix("skip="))
                except ValueError:
                    stremio_skip = 0

    page = (stremio_skip // PAGE_SIZE) + 1

    try:
        if id.startswith("custom_"):
            catalog_id = id.removeprefix("custom_")
            catalog = await db.get_custom_catalog(catalog_id)
            if not catalog or not catalog.get("visible", True):
                return {"metas": []}

            db_media_type = "tv" if media_type == "series" else "movie"
            data = await db.get_custom_catalog_items(
                catalog_id=catalog_id,
                media_type=db_media_type,
                page=page,
                page_size=PAGE_SIZE,
            )
            items = data.get("items", [])
        elif search_query:
            search_results = await db.search_documents(query=search_query, page=page, page_size=PAGE_SIZE)
            all_items = search_results.get("results", [])
            db_media_type = "tv" if media_type == "series" else "movie"
            items = [item for item in all_items if item.get("media_type") == db_media_type]
        else:
            if "latest" in id:
                sort_params = [("updated_on", "desc")]
            elif "top" in id:
                sort_params = [("rating", "desc")]
            else:
                sort_params = [("updated_on", "desc")]

            if media_type == "movie":
                data = await db.sort_movies(sort_params, page, PAGE_SIZE, genre_filter=genre_filter)
                items = data.get("movies", [])
            else:
                data = await db.sort_tv_shows(sort_params, page, PAGE_SIZE, genre_filter=genre_filter)
                items = data.get("tv_shows", [])
    except Exception as e:
        return {"metas": []}

    metas = [convert_to_stremio_meta(item) for item in items]
    return {"metas": metas}


@router.get("/{token}/meta/{media_type}/{id}.json")
async def get_meta(token: str, media_type: str, id: str, token_data: dict = Depends(verify_token)):
    if Telegram.HIDE_CATALOG:
        raise HTTPException(status_code=404, detail="Catalog disabled")
    try:
        imdb_id = id
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid Stremio ID format")

    media = await db.get_media_details(imdb_id=imdb_id)
    if not media:
        return {"meta": {}}

    meta_obj = {
        "id": id,
        "type": "series" if media.get("media_type") == "tv" else "movie",
        "name": media.get("title", ""),
        "description": media.get("description", ""),
        "year": str(media.get("release_year", "")),
        "imdbRating": str(media.get("rating", "")),
        "genres": media.get("genres", []),
        "poster": media.get("poster", ""),
        "logo": media.get("logo", ""),
        "background": media.get("backdrop", ""),
        "imdb_id": media.get("imdb_id", ""),
        "releaseInfo": str(media.get("release_year", "")),
        "moviedb_id": media.get("tmdb_id", ""),
        "cast": media.get("cast") or [],
        "runtime": media.get("runtime") or "",
    }

    if media.get("media_type") == "movie":
        released_date = format_released_date(media)
        if released_date:
            meta_obj["released"] = released_date

    # --- Add Episodes ---
    if media_type == "series" and "seasons" in media:

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        videos = []

        for season in sorted(media.get("seasons", []), key=lambda s: s.get("season_number")):
            for episode in sorted(season.get("episodes", []), key=lambda e: e.get("episode_number")):

                episode_id = f"{id}:{season['season_number']}:{episode['episode_number']}"

                videos.append({
                    "id": episode_id,
                    "title": episode.get("title", f"Episode {episode['episode_number']}"),
                    "season": season.get("season_number"),
                    "episode": episode.get("episode_number"),
                    "overview": episode.get("overview") or "No description available for this episode yet.",
                    "released": episode.get("released") or yesterday,
                    "thumbnail": episode.get("episode_backdrop") or "https://raw.githubusercontent.com/weebzone/Colab-Tools/refs/heads/main/no_episode_backdrop.png",
                    "imdb_id": episode.get("imdb_id") or media.get("imdb_id"),
                })

        meta_obj["videos"] = videos
    return {"meta": meta_obj}

@router.get("/{token}/stream/{media_type}/{id}.json")
async def get_streams(
    token: str,
    media_type: str,
    id: str,
    token_data: dict = Depends(verify_token)
):

    if token_data.get("subscription_expired"):
        from Backend.config import Telegram as _TG
        return {
            "streams": [
                {
                    "name": "🚫 Subscription Expired",
                    "title": "Your subscription has expired.\nRenew via the bot to continue watching.",
                    "url": _TG.SUBSCRIPTION_URL
                }
            ]
        }

    if token_data.get("limit_exceeded"):
        limit_type = token_data["limit_exceeded"]

        title = (
            "🚫 Daily Limit Reached – Upgrade Required"
            if limit_type == "daily"
            else "🚫 Monthly Limit Reached – Upgrade Required"
        )

        return {
            "streams": [
                {
                    "name": "Limit Reached",
                    "title": title,
                    "url": token_data["limit_video"]
                }
            ]
        }


    try:
        parts = id.split(":")
        imdb_id = parts[0]
        season_num = int(parts[1]) if len(parts) > 1 else None
        episode_num = int(parts[2]) if len(parts) > 2 else None
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid Stremio ID format")

    media_details = await db.get_media_details(
        imdb_id=imdb_id,
        season_number=season_num,
        episode_number=episode_num
    )

    if not media_details or "telegram" not in media_details:
        return {"streams": []}

    streams = []
    for quality in media_details.get("telegram", []):
        # Do not return Telegram files that were already flagged dead by the
        # link checker/admin cleanup. Dead entries can remain in the database
        # for review, but users should not see/play them in Stremio.
        if quality.get("is_dead"):
            continue

        if quality.get("id"):
            filename = quality.get("name", "")
            quality_str = quality.get("quality", "HD")
            size = quality.get("size", "")

            stream_name, stream_title = format_stream_details(
                filename, quality_str, size
            )
            mode_label, _mode_hint, _mode_score = _source_mode_label(quality)
            behavior_hints = _stream_behavior_hints(filename, size)
            # Keep ordinary cards clean: ``Telegram 2160p WEB-DL``.
            # Only add a transfer mode when it changes what the user is
            # selecting (split files, or an explicit direct/proxy choice).
            if mode_label in {"Split ZIP", "Split Video"}:
                stream_name = f"{stream_name} · {mode_label}"

            # Do not expose Telegram source links in Stremio. Source links are
            # kept only for admin/debug/dead-check use.
            original_url = f"{BASE_URL}/dl/{token}/{quality.get('id')}/video.mkv"
            proxy_url = f"{Telegram.HTTP_PROXY_URL}{original_url}" if Telegram.PROXY and Telegram.HTTP_PROXY_URL else None

            if Telegram.SHOW_PROXY_AND_NON_PROXY_BOTH and proxy_url:
                streams.append({
                    "name": f"{stream_name} · Proxy",
                    "title": stream_title,
                    "url": proxy_url,
                    "behaviorHints": behavior_hints,
                    "_sort": _stream_sort_key_from_quality(quality)
                })
                streams.append({
                    "name": f"{stream_name} · Direct",
                    "title": stream_title,
                    "url": original_url,
                    "behaviorHints": behavior_hints,
                    "_sort": _stream_sort_key_from_quality(quality)
                })
            elif proxy_url:
                streams.append({
                    "name": f"{stream_name} · Proxy",
                    "title": stream_title,
                    "url": proxy_url,
                    "behaviorHints": behavior_hints,
                    "_sort": _stream_sort_key_from_quality(quality)
                })
            else:
                streams.append({
                    "name": stream_name,
                    "title": stream_title,
                    "url": original_url,
                    "behaviorHints": behavior_hints,
                    "_sort": _stream_sort_key_from_quality(quality)
                })

    streams.sort(
        key=lambda s: s.get("_sort") or (get_resolution_priority(s.get("name", "")), 0, 0),
        reverse=True
    )
    for _s in streams:
        _s.pop("_sort", None)

    # Deduplicate stream names — Stremio collapses streams with identical names,
    # so when two files share the same caption we append (1), (2) ... to each duplicate.
    name_count: dict = {}
    for s in streams:
        name_count[s["name"]] = name_count.get(s["name"], 0) + 1

    seen: dict = {}
    for s in streams:
        if name_count[s["name"]] > 1:
            seen[s["name"]] = seen.get(s["name"], 0) + 1
            s["name"] = f"{s['name']} ({seen[s['name']]})"

    return {"streams": streams}


# ─────────────────────────────────────────────────────────────
# Subtitles endpoint
# ─────────────────────────────────────────────────────────────

_LANG_NAMES = {
    "en": "English", "si": "Sinhala", "ta": "Tamil", "hi": "Hindi",
    "fr": "French", "de": "German", "es": "Spanish", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "pt": "Portuguese",
    "ru": "Russian", "it": "Italian", "nl": "Dutch", "tr": "Turkish",
}


def _subtitle_release_key(text: str) -> set[str]:
    """Small token set used to rank subtitles against Stremio's video filename.

    The Stremio UI should stay clean and show only language badges (si/en/ta/ar),
    but exact-release behavior is kept by sorting the matching subtitle first.
    """
    text = (text or "").lower()
    text = re.sub(r"%[0-9a-f]{2}", " ", text)
    text = re.sub(r"\.(srt|vtt|ass|ssa|sub|mkv|mp4|zip|001|002|003)$", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    stop = {"sub", "subs", "subtitle", "subtitles", "srt", "vtt", "mkv", "mp4", "x264", "x265", "h264", "h265", "hevc", "aac"}
    return {t for t in text.split() if len(t) > 1 and t not in stop}


def _extract_stremio_video_filename(raw_id: str) -> str:
    """Extract filename=... from Stremio subtitle request path when present."""
    try:
        from urllib.parse import unquote, parse_qs
        if "/" not in raw_id:
            return ""
        extra = raw_id.split("/", 1)[1]
        if extra.endswith(".json"):
            extra = extra[:-5]
        extra = unquote(extra)
        parsed = parse_qs(extra, keep_blank_values=True)
        return (parsed.get("filename") or [""])[0] or ""
    except Exception:
        return ""


@router.get("/{token}/subtitles/{media_type}/{id:path}")
async def get_subtitles(
    token: str,
    media_type: str,
    id: str,
    token_data: dict = Depends(verify_token),
):
    """
    Stremio subtitles endpoint.
    UI display is intentionally compact: only the language badge/code is shown
    (si/en/ta/ar). Exact release matching is kept by ranking matching filenames
    first internally, using Stremio's optional filename=... path data.
    """
    raw_id = id
    video_filename = _extract_stremio_video_filename(raw_id)

    # Strip .json suffix and any extra Stremio path segments (filename=, videoHash=, etc.)
    id = raw_id.split("/")[0]
    if id.endswith(".json"):
        id = id[:-5]

    try:
        parts = id.split(":")
        imdb_id    = parts[0]
        season_num  = int(parts[1]) if len(parts) > 1 else None
        episode_num = int(parts[2]) if len(parts) > 2 else None
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid ID format")

    subs = await db.get_subtitles(
        imdb_id=imdb_id,
        season_number=season_num,
        episode_number=episode_num,
    )

    video_key = _subtitle_release_key(video_filename)

    def _rank(sub: dict) -> tuple[int, str]:
        sub_key = _subtitle_release_key(sub.get("name") or sub.get("release") or "")
        overlap = len(video_key & sub_key) if video_key and sub_key else 0
        return (-overlap, (sub.get("name") or "").lower())

    result = []
    for sub in sorted(subs, key=_rank):
        sub_id  = sub.get("id", "")
        lang    = (sub.get("language") or "en").lower()
        url = f"{BASE_URL}/sub/{token}/{sub_id}/subtitle.vtt"

        result.append({
            "id":   f"tg-{sub_id}",
            "url":  url,
            "lang": lang,
            # Stremio subtitle picker stays clean: only si/en/ta/ar etc.
            # Multiple same-language subtitles remain allowed; matching release
            # is placed first by _rank() above.
            "name": lang,
        })

    return {"subtitles": result}