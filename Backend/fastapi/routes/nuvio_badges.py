"""Public Nuvio stream-badge configuration and PNG assets.

Nuvio can import a JSON badge profile, then match the profile's regex rules
against Stremio stream names, titles, filenames, and parsed release metadata.
This route exposes a profile tailored to the tags emitted by this project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from Backend.config import Telegram

router = APIRouter(tags=["Nuvio stream badges"])
_BADGE_ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "nuvio_badges"
# Bump this whenever badge artwork changes so Nuvio fetches the new PNGs.
_BADGE_ASSET_REVISION: Final[str] = "icon-badges-v1"

# The order here is the order Nuvio displays matching badges on a stream card.
# Keep labels compact because Nuvio's stream chips reserve a small horizontal area.
_BADGES: Final[tuple[dict[str, str], ...]] = (
    # Resolution
    {
        "id": "4k",
        "name": "4K",
        "groupId": "resolution",
        "pattern": r"(?i)(?:\[4K\]|\b(?:2160p|4k|uhd)\b)",
        "tagColor": "#FF151515",
        "borderColor": "#FFE5E7EB",
    },
    {
        "id": "1440p",
        "name": "1440P",
        "groupId": "resolution",
        "pattern": r"(?i)(?:\[1440P\]|\b1440p\b)",
        "tagColor": "#FF151515",
        "borderColor": "#FFD1D5DB",
    },
    {
        "id": "1080p",
        "name": "1080P",
        "groupId": "resolution",
        "pattern": r"(?i)(?:\[1080P\]|\b(?:1080p|fhd)\b)",
        "tagColor": "#FF151515",
        "borderColor": "#FFD1D5DB",
    },
    {
        "id": "720p",
        "name": "720P",
        "groupId": "resolution",
        "pattern": r"(?i)(?:\[720P\]|\b720p\b)",
        "tagColor": "#FF151515",
        "borderColor": "#FFD1D5DB",
    },
    {
        "id": "480p",
        "name": "480P",
        "groupId": "resolution",
        "pattern": r"(?i)(?:\[480P\]|\b480p\b)",
        "tagColor": "#FF151515",
        "borderColor": "#FFD1D5DB",
    },
    # Source / release type
    {
        "id": "webdl",
        "name": "WebDL",
        "groupId": "source",
        "pattern": r"(?i)(?:\[WebDL\]|\bweb[ ._-]*dl\b|\bwebdl\b)",
        "tagColor": "#FF0B1B2B",
        "borderColor": "#FF1686DE",
    },
    {
        "id": "webrip",
        "name": "WebRip",
        "groupId": "source",
        "pattern": r"(?i)(?:\[WebRip\]|\bweb[ ._-]*rip\b|\bwebrip\b)",
        "tagColor": "#FF0B1B2B",
        "borderColor": "#FF14B8A6",
    },
    {
        "id": "bluray",
        "name": "BluRay",
        "groupId": "source",
        "pattern": r"(?i)(?:\[BluRay\]|\b(?:blu[ ._-]*ray|bluray|bdrip|brrip)\b)",
        "tagColor": "#FF0B1B2B",
        "borderColor": "#FF60A5FA",
    },
    {
        "id": "remux",
        "name": "REMUX",
        "groupId": "source",
        "pattern": r"(?i)(?:\[REMUX\]|\b(?:remux|bdremux)\b)",
        "tagColor": "#FF23150A",
        "borderColor": "#FFF59E0B",
    },
    {
        "id": "hdrip",
        "name": "HDRip",
        "groupId": "source",
        "pattern": r"(?i)(?:\[HDRip\]|\bhdrip\b)",
        "tagColor": "#FF0B1B2B",
        "borderColor": "#FF38BDF8",
    },
    {
        "id": "hdtv",
        "name": "HDTV",
        "groupId": "source",
        "pattern": r"(?i)(?:\[HDTV\]|\bhdtv(?:rip)?\b)",
        "tagColor": "#FF0B1B2B",
        "borderColor": "#FF38BDF8",
    },
    # Languages
    {
        "id": "hin",
        "name": "HIN",
        "groupId": "language",
        "pattern": r"(?i)(?:\[HIN\]|\b(?:hindi|hin)\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    {
        "id": "tel",
        "name": "TEL",
        "groupId": "language",
        "pattern": r"(?i)(?:\[TEL\]|\b(?:telugu|tel)\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    {
        "id": "tam",
        "name": "TAM",
        "groupId": "language",
        "pattern": r"(?i)(?:\[TAM\]|\b(?:tamil|tam)\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    {
        "id": "mal",
        "name": "MAL",
        "groupId": "language",
        "pattern": r"(?i)(?:\[MAL\]|\b(?:malayalam|mal)\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    {
        "id": "kan",
        "name": "KAN",
        "groupId": "language",
        "pattern": r"(?i)(?:\[KAN\]|\b(?:kannada|kan)\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    {
        "id": "ben",
        "name": "BEN",
        "groupId": "language",
        "pattern": r"(?i)(?:\[BEN\]|\b(?:bengali|bangla|ben)\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    {
        "id": "sin",
        "name": "SIN",
        "groupId": "language",
        "pattern": r"(?i)(?:\[SIN\]|\b(?:sinhala|sin)\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    {
        "id": "eng",
        "name": "ENG",
        "groupId": "language",
        "pattern": r"(?i)(?:\[ENG\]|\b(?:english|eng)\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    {
        "id": "multi",
        "name": "MULTI",
        "groupId": "language",
        "pattern": r"(?i)(?:\[MULTI\]|\bmulti(?:[ ._-]*(?:audio|lang))?\b)",
        "tagColor": "#FF28180F",
        "borderColor": "#FFFB923C",
    },
    # Codec and HDR
    {
        "id": "hevc",
        "name": "HEVC",
        "groupId": "video",
        "pattern": r"(?i)(?:\[HEVC\]|\b(?:hevc|x265|h[ ._-]?265)\b)",
        "tagColor": "#FF08221B",
        "borderColor": "#FF10B981",
    },
    {
        "id": "avc",
        "name": "AVC",
        "groupId": "video",
        "pattern": r"(?i)(?:\[AVC\]|\b(?:avc|x264|h[ ._-]?264)\b)",
        "tagColor": "#FF08221B",
        "borderColor": "#FF10B981",
    },
    {
        "id": "av1",
        "name": "AV1",
        "groupId": "video",
        "pattern": r"(?i)(?:\[AV1\]|\bav1\b)",
        "tagColor": "#FF08221B",
        "borderColor": "#FF10B981",
    },
    {
        "id": "dv",
        "name": "DV",
        "groupId": "video",
        "pattern": r"(?i)(?:\[DV\]|\b(?:dolby[ ._-]*vision|dovi|dv)\b)",
        "tagColor": "#FF24132E",
        "borderColor": "#FFC084FC",
    },
    {
        "id": "hdr10plus",
        "name": "HDR10+",
        "groupId": "video",
        "pattern": r"(?i)(?:\[HDR10\+\]|\bhdr10\+\b)",
        "tagColor": "#FF24132E",
        "borderColor": "#FFC084FC",
    },
    {
        "id": "hdr10",
        "name": "HDR10",
        "groupId": "video",
        "pattern": r"(?i)(?:\[HDR10\]|\bhdr10\b)",
        "tagColor": "#FF24132E",
        "borderColor": "#FFC084FC",
    },
    {
        "id": "hdr",
        "name": "HDR",
        "groupId": "video",
        "pattern": r"(?i)(?:\[HDR\]|\bhdr\b)",
        "tagColor": "#FF24132E",
        "borderColor": "#FFC084FC",
    },
    # Audio
    {
        "id": "ddplus",
        "name": "DD+",
        "groupId": "audio",
        "pattern": r"(?i)(?:\[DD\+\]|\b(?:dd\+|ddp|eac3|e[ ._-]*ac[ ._-]*3)\b)",
        "tagColor": "#FF102333",
        "borderColor": "#FF22D3EE",
    },
    {
        "id": "dd",
        "name": "DD",
        "groupId": "audio",
        "pattern": r"(?i)(?:\[DD\]|\b(?:dolby[ ._-]*digital|ac3)\b)",
        "tagColor": "#FF102333",
        "borderColor": "#FF22D3EE",
    },
    {
        "id": "atmos",
        "name": "ATMOS",
        "groupId": "audio",
        "pattern": r"(?i)(?:\[ATMOS\]|\b(?:atmos|dolby[ ._-]*atmos)\b)",
        "tagColor": "#FF102333",
        "borderColor": "#FF22D3EE",
    },
    {
        "id": "truehd",
        "name": "TrueHD",
        "groupId": "audio",
        "pattern": r"(?i)(?:\[TrueHD\]|\btrue[ ._-]*hd\b)",
        "tagColor": "#FF102333",
        "borderColor": "#FF22D3EE",
    },
    {
        "id": "dtsx",
        "name": "DTS:X",
        "groupId": "audio",
        "pattern": r"(?i)(?:\[DTS:X\]|\b(?:dts[ ._-]*x|dtsx)\b)",
        "tagColor": "#FF102333",
        "borderColor": "#FF22D3EE",
    },
    {
        "id": "dts",
        "name": "DTS",
        "groupId": "audio",
        "pattern": r"(?i)(?:\[DTS\]|\bdts\b)",
        "tagColor": "#FF102333",
        "borderColor": "#FF22D3EE",
    },
    {
        "id": "51",
        "name": "5.1",
        "groupId": "audio",
        "pattern": r"(?i)(?:\[5\.1\]|\b5\.1\b)",
        "tagColor": "#FF151515",
        "borderColor": "#FFD1D5DB",
    },
    {
        "id": "71",
        "name": "7.1",
        "groupId": "audio",
        "pattern": r"(?i)(?:\[7\.1\]|\b7\.1\b)",
        "tagColor": "#FF151515",
        "borderColor": "#FFD1D5DB",
    },
)

_GROUPS: Final[tuple[dict[str, str], ...]] = (
    {"id": "resolution", "name": "Resolution", "color": "#FFE5E7EB"},
    {"id": "source", "name": "Source", "color": "#FF1686DE"},
    {"id": "language", "name": "Language", "color": "#FFFB923C"},
    {"id": "video", "name": "Video", "color": "#FF10B981"},
    {"id": "audio", "name": "Audio", "color": "#FF22D3EE"},
)


def _public_base_url(request: Request) -> str:
    """Prefer the configured public app URL; fall back to the current request."""
    configured = str(getattr(Telegram, "BASE_URL", "") or "").strip().rstrip("/")
    if configured.startswith(("https://", "http://")):
        return configured
    return str(request.base_url).rstrip("/")


def _badge_asset_path(badge_id: str) -> Path | None:
    """Return a bundled PNG label only for a declared badge ID."""
    if not any(entry["id"] == badge_id for entry in _BADGES):
        return None
    candidate = _BADGE_ASSET_DIR / f"{badge_id}.png"
    return candidate if candidate.is_file() else None


@router.get("/nuvio-badges.json", include_in_schema=False)
async def nuvio_badge_rules(request: Request) -> JSONResponse:
    """Badge profile URL to import in Nuvio's Settings → Streams screen."""
    base_url = _public_base_url(request)
    filters = [
        {
            **badge,
            "type": "filter",
            "imageURL": (
                f"{base_url}/nuvio-badges/{badge['id']}.png"
                f"?v={_BADGE_ASSET_REVISION}"
            ),
            "isEnabled": True,
            "tagStyle": "filled and bordered",
            "textColor": "#FFF8FAFC",
        }
        for badge in _BADGES
    ]
    groups = [
        {
            **group,
            "borderColor": group["color"],
            "isExpanded": True,
        }
        for group in _GROUPS
    ]
    return JSONResponse(
        {"filters": filters, "groups": groups},
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/nuvio-badges/{badge_id}.png", include_in_schema=False, name="nuvio_badge_png")
async def nuvio_badge_png(badge_id: str) -> Response:
    """Bundled transparent PNG label used by the Nuvio badge profile."""
    asset_path = _badge_asset_path(badge_id)
    if asset_path is None:
        return Response(status_code=404, content="Unknown badge")
    return FileResponse(
        asset_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300, must-revalidate"},
    )
