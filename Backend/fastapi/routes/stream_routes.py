import math
import secrets
import mimetypes
import time
import json
import zipfile
import hashlib
import os
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse

from collections import deque

from Backend import db
from Backend.helper.encrypt import decode_string
from Backend.helper.exceptions import InvalidHash
from Backend.helper.custom_dl import ByteStreamer, ACTIVE_STREAMS, RECENT_STREAMS, get_adaptive_chunk_size
from Backend.helper.split_archive import (
    archive_cache_key,
    choose_video_member,
    extract_member_to_file,
    safe_cache_filename,
    split_zip_cache_root,
)
from Backend.pyrofork.bot import (
    work_loads, multi_clients, client_dc_map, client_failures, client_avg_mbps,
    client_chunk_loads, client_rtt_ms,
)
from Backend.config import Telegram
from Backend.logger import LOGGER
from Backend.fastapi.security.tokens import verify_token
import asyncio

router = APIRouter(tags=["Streaming"])

_streamer_by_client: Dict = {}
_rr_counter: int = 0
_slot_rr_counter: int = 0

_title_cache: Dict[str, tuple] = {}
_TITLE_CACHE_TTL = 300

_split_zip_locks: Dict[str, asyncio.Lock] = {}
_split_zip_meta_cache: Dict[str, tuple] = {}
_split_stream_block_locks: Dict[str, asyncio.Lock] = {}
_split_stream_cache_last_prune: float = 0.0

# A playback is pinned to one healthy Telegram bot for its whole short-lived
# HTTP range session. Extra bots are reserved for other viewers, which avoids
# cross-bot range striping and makes seeks predictable.
_stream_affinities: Dict[str, tuple[int, float]] = {}
_stream_affinity_last_prune: float = 0.0


def _get_split_zip_lock(key: str) -> asyncio.Lock:
    lock = _split_zip_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _split_zip_locks[key] = lock
    return lock


def make_json_safe(obj):
    if isinstance(obj, deque):
        return list(obj)
    if isinstance(obj, (set, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="ignore")
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [make_json_safe(v) for v in obj]
    return obj


def parse_range_header(range_header: str, file_size: int):
    """
    Parse HTTP Range header.

    Supports:
    bytes=1000-2000
    bytes=1000-
    bytes=-2000
    """
    if not range_header:
        return 0, file_size - 1

    try:
        value = range_header.replace("bytes=", "").strip()
        start_str, end_str = value.split("-")

        if start_str == "":
            length = int(end_str)
            start = file_size - length
            end = file_size - 1
        elif end_str == "":
            start = int(start_str)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str)

    except Exception:
        raise HTTPException(
            status_code=416,
            detail="Invalid Range header",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    if start < 0:
        start = 0

    if end >= file_size:
        end = file_size - 1

    if end < start:
        raise HTTPException(
            status_code=416,
            detail="Requested Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    return start, end


def _client_router_score(idx: int) -> float:
    """Lower score is better for the Telegram stream router.

    Whole-stream load alone is not enough for split media: one viewer can be
    pulling several byte ranges at once.  The short-lived chunk counter and
    measured request latency keep work moving across all healthy bot accounts.
    """
    stream_load = float(work_loads.get(idx, 0) or 0)
    chunk_load = float(client_chunk_loads.get(idx, 0) or 0)
    failures = float(client_failures.get(idx, 0) or 0)
    mbps = float(client_avg_mbps.get(idx, 0.0) or 0.0)
    rtt_ms = float(client_rtt_ms.get(idx, 0.0) or 0.0)
    speed_bonus = min(mbps / 8.0, 4.5)
    latency_penalty = min(rtt_ms / 350.0, 3.0)
    return (stream_load * 2.0) + (chunk_load * 3.0) + (failures * 6.0) + latency_penalty - speed_bonus


def _mark_client_chunk_started(client_index: int) -> None:
    client_chunk_loads[client_index] = max(0, int(client_chunk_loads.get(client_index, 0) or 0)) + 1


def _mark_client_chunk_finished(client_index: int, payload_size: int = 0, elapsed: float = 0.0) -> None:
    client_chunk_loads[client_index] = max(0, int(client_chunk_loads.get(client_index, 0) or 0) - 1)
    if payload_size <= 0 or elapsed <= 0:
        return
    mbps = (float(payload_size) / (1024 * 1024)) / max(float(elapsed), 0.001)
    previous = float(client_avg_mbps.get(client_index, 0.0) or 0.0)
    client_avg_mbps[client_index] = mbps if previous <= 0 else (0.78 * previous + 0.22 * mbps)
    sample_ms = float(elapsed) * 1000.0
    old_rtt = float(client_rtt_ms.get(client_index, 0.0) or 0.0)
    client_rtt_ms[client_index] = sample_ms if old_rtt <= 0 else (0.72 * old_rtt + 0.28 * sample_ms)


def _choose_fast_pool_slot(pool: list[dict], sequence_index: int) -> dict:
    """Pick the least-busy, fastest healthy slot without pinning a stream to one bot."""
    global _slot_rr_counter
    candidates = [slot for slot in pool if slot.get("client_index") in multi_clients]
    if not candidates:
        raise HTTPException(status_code=503, detail="No healthy Telegram stream client is available")
    scores = {id(slot): _client_router_score(int(slot["client_index"])) for slot in candidates}
    best = min(scores.values())
    tied = [slot for slot in candidates if abs(scores[id(slot)] - best) < 0.0001]
    tied.sort(key=lambda slot: int(slot["client_index"]))
    selected = tied[(int(sequence_index) + _slot_rr_counter) % len(tied)]
    _slot_rr_counter = (_slot_rr_counter + 1) % max(1, len(tied))
    return selected


def _route_candidates(target_dc: int = 0, exclude: set[int] | None = None) -> list[int]:
    """Return DC-aware candidates for the Telegram stream router."""
    exclude = exclude or set()
    if target_dc and int(target_dc) > 0:
        same_dc = [
            idx for idx, dc in client_dc_map.items()
            if idx in multi_clients and idx not in exclude and int(dc or 0) == int(target_dc)
        ]
        if same_dc:
            return same_dc
    return [idx for idx in multi_clients.keys() if idx not in exclude]


def _ordered_route_clients(target_dc: int = 0, preferred_index: int | None = None, max_count: int | None = None) -> list[int]:
    """Order clients for one stream/range: preferred, same DC, then fallback."""
    if not multi_clients:
        return []

    ordered: list[int] = []
    if preferred_index in multi_clients:
        ordered.append(int(preferred_index))

    same_dc = []
    if target_dc and int(target_dc) > 0:
        same_dc = [
            idx for idx, dc in client_dc_map.items()
            if idx in multi_clients and idx not in ordered and int(dc or 0) == int(target_dc)
        ]
        same_dc.sort(key=_client_router_score)
        ordered.extend(same_dc)

    fallback = [idx for idx in multi_clients if idx not in ordered]
    fallback.sort(key=_client_router_score)
    ordered.extend(fallback)

    if max_count is not None:
        return ordered[: max(1, min(int(max_count), len(ordered)))]
    return ordered


def select_best_client(target_dc: int = 0) -> int:
    """Pick the best available Telegram client using the stream router.

    target_dc > 0 → prefer clients already connected to that Telegram DC.
    target_dc == 0 → choose the lowest load/failure client, with speed history.
    Ties are round-robin so one bot does not get all first-play requests.
    """
    global _rr_counter

    matching = _route_candidates(target_dc)
    if not matching:
        return 0

    min_score = min(_client_router_score(i) for i in matching)
    tied = sorted(i for i in matching if abs(_client_router_score(i) - min_score) < 0.0001)

    selected = tied[_rr_counter % len(tied)]
    _rr_counter = (_rr_counter + 1) % max(len(multi_clients), 1)

    LOGGER.debug(
        "Telegram stream router target_dc=%s selected_client=%s score=%.2f pool=%s same_dc=%s",
        target_dc, selected, min_score, len(matching), bool(target_dc and len(matching) < len(multi_clients)),
    )
    return selected


def _stream_affinity_seconds() -> int:
    """How long a viewer keeps the same bot during playback and seeking."""
    raw = int(getattr(Telegram, "STREAM_AFFINITY_SECONDS", 900) or 900)
    return max(60, min(raw, 3600))


def _stream_viewer_key(request: Request, token: str, media_key: str) -> str:
    """Create an opaque per-viewer/per-media affinity key without logging secrets."""
    forwarded = (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    host = forwarded or (request.client.host if request.client else "unknown")
    agent = request.headers.get("user-agent", "")[:160]
    raw = f"{token}|{media_key}|{host}|{agent}"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _prune_stream_affinities() -> None:
    global _stream_affinity_last_prune
    now = time.time()
    if now - _stream_affinity_last_prune < 30:
        return
    _stream_affinity_last_prune = now
    for key, (client_index, expires_at) in list(_stream_affinities.items()):
        if expires_at <= now or client_index not in multi_clients:
            _stream_affinities.pop(key, None)


def _select_affinity_client(affinity_key: str, target_dc: int = 0) -> tuple[int, str]:
    """Pick a bot once per viewer session, then keep every seek on that bot.

    A bot with repeated recent errors is allowed to fail over on the next range.
    This keeps the normal path one-stream/one-bot while still recovering from a
    bot disconnect, FloodWait, or temporary Telegram/DC error.
    """
    _prune_stream_affinities()
    now = time.time()
    current = _stream_affinities.get(affinity_key)
    if current:
        client_index, _expires = current
        if client_index in multi_clients and int(client_failures.get(client_index, 0) or 0) < 4:
            _stream_affinities[affinity_key] = (client_index, now + _stream_affinity_seconds())
            return client_index, "sticky"
        _stream_affinities.pop(affinity_key, None)

    selected = select_best_client(target_dc)
    _stream_affinities[affinity_key] = (selected, now + _stream_affinity_seconds())
    return selected, "new"


def _release_stream_affinity(affinity_key: str) -> None:
    """Forget a failed route so the next HTTP range can select a replacement."""
    _stream_affinities.pop(affinity_key, None)


async def decay_client_failures() -> None:
    """Every 5 minutes reduce each client's failure count by 1 (floor 0).

    This lets bots self-recover after a temporary DC issue without manual
    intervention.  The coroutine is started once as a background task on
    first import.
    """
    while True:
        await asyncio.sleep(300)  # 5 minutes
        for k in list(client_failures):
            if client_failures.get(k, 0) > 0:
                client_failures[k] = max(0, client_failures[k] - 1)
                LOGGER.debug("Failure decay: client %s failures → %s", k, client_failures[k])



async def track_usage_from_stats(stream_id: str, token: str, token_data: dict):
    await asyncio.sleep(2)
    
    limits = token_data.get("limits", {}) if token_data else {}
    usage = token_data.get("usage", {}) if token_data else {}
    
    daily_limit_gb = limits.get("daily_limit_gb")
    monthly_limit_gb = limits.get("monthly_limit_gb")
    
    initial_daily_bytes = usage.get("daily", {}).get("bytes", 0)
    initial_monthly_bytes = usage.get("monthly", {}).get("bytes", 0)
    
    last_tracked_bytes = 0
    update_interval = 10
    
    try:
        while True:
            await asyncio.sleep(update_interval)
            stream_info = ACTIVE_STREAMS.get(stream_id)
            if not stream_info:
                for rec in RECENT_STREAMS:
                    if rec.get("stream_id") == stream_id:
                        final_bytes = rec.get("total_bytes", 0)
                        delta = final_bytes - last_tracked_bytes
                        if delta > 0:
                            try:
                                await db.update_token_usage(token, delta)
                                LOGGER.debug(f"Final usage update for {stream_id}: {delta} bytes")
                            except Exception as e:
                                LOGGER.error(f"Final usage update failed: {e}")
                        break
                return
            
            current_bytes = stream_info.get("total_bytes", 0)
            delta = current_bytes - last_tracked_bytes
            
            if delta > 0:
                try:
                    await db.update_token_usage(token, delta)
                    last_tracked_bytes = current_bytes
                    LOGGER.debug(f"Updated usage for {stream_id}: +{delta} bytes (total: {current_bytes})")
                except Exception as e:
                    LOGGER.error(f"Periodic usage update failed: {e}")
            
            # Check limits (don't stop stream, just log - client manages connection)
            if daily_limit_gb and daily_limit_gb > 0:
                current_daily_gb = (initial_daily_bytes + current_bytes) / (1024 ** 3)
                if current_daily_gb >= daily_limit_gb:
                    LOGGER.debug(f"Daily limit reached for token, stream {stream_id} may be blocked by verify_token")
            
            if monthly_limit_gb and monthly_limit_gb > 0:
                current_monthly_gb = (initial_monthly_bytes + current_bytes) / (1024 ** 3)
                if current_monthly_gb >= monthly_limit_gb:
                    LOGGER.debug(f"Monthly limit reached for token, stream {stream_id} may be blocked by verify_token")
                    
    except asyncio.CancelledError:
        stream_info = ACTIVE_STREAMS.get(stream_id)
        if stream_info:
            current_bytes = stream_info.get("total_bytes", 0)
            delta = current_bytes - last_tracked_bytes
            if delta > 0:
                try:
                    await db.update_token_usage(token, delta)
                    LOGGER.info(f"Cancelled - final update for {stream_id}: {delta} bytes")
                except Exception as e:
                    LOGGER.error(f"Cancelled usage update failed: {e}")




def _telegram_chat_id_from_part(part: dict) -> int:
    raw_chat_id = str(part.get("chat_id", "")).strip()
    if not raw_chat_id:
        raise HTTPException(status_code=400, detail="Split ZIP part is missing chat_id")
    if raw_chat_id.startswith("-100"):
        return int(raw_chat_id)
    return int(f"-100{raw_chat_id}")


async def _resolve_split_zip_messages(decoded: dict, preferred_index: int | None = None):
    """Fetch Telegram messages for the split archive parts in order."""
    parts = decoded.get("parts") or []
    if not parts:
        raise HTTPException(status_code=400, detail="Split ZIP stream has no parts")

    parts = sorted(parts, key=lambda p: int(p.get("part", 0)))
    part_numbers = [int(p.get("part", 0)) for p in parts]
    expected = list(range(1, len(parts) + 1))
    if part_numbers != expected:
        raise HTTPException(
            status_code=409,
            detail=f"Split ZIP parts are incomplete. Found {part_numbers}, expected {expected}",
        )

    if not multi_clients:
        raise HTTPException(status_code=503, detail="No Telegram clients are connected")

    client_index = int(preferred_index) if preferred_index in multi_clients else select_best_client(0)
    tg_client = multi_clients.get(client_index) or next(iter(multi_clients.values()))

    messages = []
    for part in parts:
        chat_id = _telegram_chat_id_from_part(part)
        msg_id = int(part.get("msg_id"))
        msg = await tg_client.get_messages(chat_id, msg_id)
        if not msg or getattr(msg, "empty", False):
            raise HTTPException(
                status_code=404,
                detail=f"Split ZIP part {int(part.get('part', 0)):03d} was not found in Telegram",
            )
        messages.append(msg)

    return client_index, tg_client, messages, parts


def _pick_zip_video_entry(entries: list, requested_name: str | None = None):
    from Backend.helper.split_archive import is_video_member

    playable = [e for e in entries if not e.is_dir() and is_video_member(e.filename)]
    if requested_name:
        for entry in playable:
            if entry.filename == requested_name:
                return entry
        for entry in playable:
            if entry.filename.lower() == requested_name.lower():
                return entry
        raise HTTPException(status_code=404, detail="Requested video file was not found inside the ZIP")

    if not playable:
        raise HTTPException(status_code=422, detail="No playable video file found inside the ZIP archive")
    return max(playable, key=lambda info: int(info.file_size or 0))


def _split_zip_meta_cache_key(decoded: dict, parts: list) -> str:
    return archive_cache_key(parts, decoded.get("archive_name") or decoded.get("title") or "split.zip")


def _get_cached_split_zip_meta(cache_key: str) -> dict | None:
    cached = _split_zip_meta_cache.get(cache_key)
    if not cached:
        return None
    expires_at, meta = cached
    if time.time() >= float(expires_at):
        _split_zip_meta_cache.pop(cache_key, None)
        return None
    return dict(meta)


def _set_cached_split_zip_meta(cache_key: str, meta: dict) -> None:
    ttl = int(getattr(Telegram, "VIRTUAL_ZIP_META_CACHE_SECONDS", 1800) or 0)
    if ttl <= 0:
        return
    _split_zip_meta_cache[cache_key] = (time.time() + ttl, dict(meta))
    # Keep the process cache tiny. Each item is only ZIP metadata, but Render RAM is small.
    while len(_split_zip_meta_cache) > 128:
        oldest_key = min(_split_zip_meta_cache, key=lambda k: _split_zip_meta_cache[k][0])
        _split_zip_meta_cache.pop(oldest_key, None)


def _zip_compression_label(compress_type: int) -> str:
    try:
        if int(compress_type) == zipfile.ZIP_STORED:
            return "stored"
        if int(compress_type) == zipfile.ZIP_DEFLATED:
            return "deflated"
        if int(compress_type) == getattr(zipfile, "ZIP_BZIP2", -1):
            return "bzip2"
        if int(compress_type) == getattr(zipfile, "ZIP_LZMA", -1):
            return "lzma"
    except Exception:
        pass
    return str(compress_type)


# Player-friendly response window for split ZIP / split video streams.
# This only caps each HTTP response; it does NOT load the full window into RAM.
# Use SPLIT_STREAM_WINDOW_MB=64/100/128 to tune for your host/network.
def _split_stream_window_bytes() -> int:
    mb = int(getattr(Telegram, "SPLIT_STREAM_WINDOW_MB", getattr(Telegram, "VIRTUAL_ZIP_INITIAL_RANGE_MB", 50)) or 50)
    mb = max(8, min(mb, 256))
    return mb * 1024 * 1024


def _split_stream_window_label() -> str:
    return f"{_split_stream_window_bytes() // (1024 * 1024)}mb"


def _player_resume_headers(file_size: int, stream_key: str = "") -> dict:
    """Headers that help Stremio/VLC/ExoPlayer treat Telegram streams as seekable.

    The important bit for resume is stable byte-range support.  ETag is based on
    the Telegram message/encoded stream key and file size so a player can resume
    the same URL after closing/reopening.
    """
    base = hashlib.sha1(f"{stream_key}:{int(file_size or 0)}".encode("utf-8")).hexdigest()[:24]
    return {
        "Accept-Ranges": "bytes",
        "ETag": f'W/"tg-{base}"',
        "X-Resume-Support": "bytes",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    }


def _cap_player_range(
    range_header: str,
    file_size: int,
    window_bytes: int | None = None,
    seek_window_bytes: int | None = None,
):
    """Cap open-ended player reads into responsive 206 windows.

    Cold play uses a modest startup window. A seek (a non-zero byte offset)
    deliberately uses a smaller window so Stremio/VLC can discard old buffer
    work and begin the newly requested position with minimal delay.
    """
    window_bytes = int(window_bytes or _split_stream_window_bytes())
    window_bytes = max(8 * 1024 * 1024, min(window_bytes, 256 * 1024 * 1024))
    seek_window_bytes = int(seek_window_bytes or window_bytes)
    seek_window_bytes = max(4 * 1024 * 1024, min(seek_window_bytes, window_bytes))
    if file_size <= 0:
        return range_header, False

    if not range_header or not range_header.startswith("bytes="):
        end = min(file_size - 1, window_bytes - 1)
        return f"bytes=0-{end}", True

    try:
        value = range_header.replace("bytes=", "", 1).strip()
        start_str, end_str = value.split("-", 1)
        # Keep suffix probes untouched: media players use these to read indexes.
        if start_str == "":
            return range_header, False
        start = max(0, int(start_str))
        if start >= file_size:
            return range_header, False
        requested_open_end = end_str == ""
        requested_end = file_size - 1 if requested_open_end else min(int(end_str), file_size - 1)
        active_window = seek_window_bytes if start > 0 else window_bytes
        window_end = min(file_size - 1, start + active_window - 1)
        if requested_open_end or requested_end > window_end:
            return f"bytes={start}-{window_end}", True
    except Exception:
        return range_header, False
    return range_header, False


def _is_fast_start_request(original_range_header: str, start: int) -> bool:
    if not original_range_header:
        return start == 0
    return original_range_header.replace(" ", "").lower().startswith("bytes=0-")


def _stream_router_client_limit(client_count: int) -> int:
    """Return the usable bot count without baking a fixed pool size into code.

    ``STREAM_MAX_PARALLEL_BOTS=0`` (the default) means every healthy connected
    bot can be selected.  A positive value is only an optional host safety cap
    for deployments that deliberately want to restrict session/memory use.
    """
    client_count = max(1, int(client_count or 1))
    configured_limit = int(getattr(Telegram, "STREAM_MAX_PARALLEL_BOTS", 0) or 0)
    if configured_limit > 0:
        return max(1, min(client_count, configured_limit))
    return client_count


def _adaptive_stream_tuning(*, fast_start: bool, split_stream: bool = False,
                            current_registered: bool = False) -> tuple[int, int]:
    """Return low-RAM prefetch tuning for a single assigned Telegram bot.

    Bot selection is viewer-level, not chunk-level: one playback stays on one
    bot. Parallel reads here are multiple GetFile requests through that same
    bot/session only. That keeps seeks stable while other viewers are balanced
    onto other available bots.
    """
    live_statuses = {"active", "starting"}
    active_count = sum(
        1 for info in ACTIVE_STREAMS.values()
        if str(info.get("status", "active")).lower() in live_statuses
    )
    if not current_registered:
        active_count += 1
    active_count = max(1, active_count)

    configured_workers = int(getattr(Telegram, "STREAM_PREFETCH_WORKERS", 2) or 2)
    configured_prefetch = int(getattr(Telegram, "STREAM_PREFETCH_BLOCKS", 3) or 3)
    configured_workers = max(1, min(configured_workers, 4))
    configured_prefetch = max(2, min(configured_prefetch, 12))

    # Keep the first byte priority above all else. Under several viewers, fall
    # back to one in-flight request per stream to share Telegram fairly.
    if fast_start or active_count >= max(2, len(multi_clients)):
        return 1, min(configured_prefetch, 3)

    workers = min(configured_workers, 2 if split_stream else configured_workers)
    prefetch = max(workers + 1, configured_prefetch)
    return workers, prefetch


def _cap_split_zip_player_range(range_header: str, file_size: int):
    """Seek-aware range windows for direct split files and stored ZIP entries."""
    seek_mb = int(getattr(Telegram, "SPLIT_SEEK_WINDOW_MB", 16) or 16)
    return _cap_player_range(
        range_header,
        file_size,
        _split_stream_window_bytes(),
        max(4, min(seek_mb, 128)) * 1024 * 1024,
    )


def _split_zip_headers(file_name: str, mime_type: str, req_length: int, range_header: str,
                       start: int, end: int, file_size: int, compression: str, cache_status: str) -> dict:
    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(req_length),
        "Content-Disposition": f'inline; filename="{file_name}"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
        "X-Archive-Mode": "virtual-telegram-zip",
        "X-Zip-Compression": compression,
        "X-Zip-Meta-Cache": cache_status,
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag, X-Resume-Support, X-Archive-Mode, X-Zip-Compression, X-Zip-Meta-Cache, X-Stream-Router",
    }
    headers.update(_player_resume_headers(file_size, file_name))
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    return headers


def _register_virtual_stream(stream_id: str, decoded: dict, meta: dict, client_index, parts: list) -> None:
    first_part = (parts or decoded.get("parts") or [{}])[0]
    now = time.time()
    ACTIVE_STREAMS[stream_id] = {
        "stream_id": stream_id,
        "msg_id": first_part.get("msg_id"),
        "chat_id": _telegram_chat_id_from_part(first_part) if first_part.get("chat_id") else None,
        "client_index": client_index,
        "dc_id": "virtual_split_file" if meta.get("source_type") == "split_file_virtual" else "virtual_zip",
        "status": "active",
        "total_bytes": 0,
        "start_ts": now,
        "last_ts": now,
        "instant_mbps": 0.0,
        "avg_mbps": 0.0,
        "peak_mbps": 0.0,
        "recent_measurements": deque(maxlen=20),
        "meta": meta,
    }
    try:
        if isinstance(client_index, int):
            work_loads[client_index] += 1
    except Exception:
        pass


def _update_virtual_stream_stats(stream_id: str, chunk_len: int, last_measure: dict) -> None:
    entry = ACTIVE_STREAMS.get(stream_id)
    if not entry:
        return
    now_ts = time.time()
    entry["total_bytes"] += int(chunk_len)
    entry["last_ts"] = now_ts

    elapsed = max(now_ts - entry.get("start_ts", now_ts), 0.001)
    measure_elapsed = max(now_ts - last_measure.get("ts", now_ts), 0.001)
    measure_bytes = entry["total_bytes"] - last_measure.get("bytes", 0)
    instant_mbps = (measure_bytes / (1024 * 1024)) / measure_elapsed
    entry["instant_mbps"] = instant_mbps
    entry["avg_mbps"] = (entry["total_bytes"] / (1024 * 1024)) / elapsed
    entry["peak_mbps"] = max(entry.get("peak_mbps", 0.0), instant_mbps)
    entry["recent_measurements"].append((now_ts, instant_mbps))
    last_measure["ts"] = now_ts
    last_measure["bytes"] = entry["total_bytes"]


async def _finalize_virtual_stream(stream_id: str, status: str = "finished"):
    entry = ACTIVE_STREAMS.get(stream_id)
    if not entry:
        return
    if entry.get("status") == "active":
        entry["status"] = status
    entry["end_ts"] = time.time()
    entry["duration"] = entry["end_ts"] - entry.get("start_ts", entry["end_ts"])
    try:
        duration = max(float(entry.get("duration") or 0.0), 1e-6)
        total_bytes = int(entry.get("total_bytes") or 0)
        avg_mbps = (total_bytes / (1024 * 1024)) / duration
        entry["avg_mbps"] = avg_mbps
        entry["peak_mbps"] = max(float(entry.get("peak_mbps") or 0.0), float(entry.get("instant_mbps") or 0.0), avg_mbps)
        idx = entry.get("client_index")
        if isinstance(idx, int):
            prev = float(client_avg_mbps.get(idx, 0.0) or 0.0)
            client_avg_mbps[idx] = avg_mbps if prev <= 0 else (0.65 * prev + 0.35 * avg_mbps)
    except Exception:
        pass
    try:
        if isinstance(entry.get("client_index"), int):
            idx = entry["client_index"]
            work_loads[idx] = max(0, int(work_loads.get(idx, 0) or 0) - 1)
    except Exception:
        pass
    try:
        asyncio.create_task(db.log_stream_stats(entry))
    except Exception:
        pass
    RECENT_STREAMS.appendleft(ACTIVE_STREAMS.pop(stream_id, entry))


async def _virtual_file_range_generator(request: Request, reader, absolute_start: int, absolute_end: int, stream_id: str):
    last_measure = {"ts": time.time(), "bytes": 0}
    current = int(absolute_start)
    absolute_end = int(absolute_end)
    try:
        while current <= absolute_end:
            if await request.is_disconnected():
                if stream_id in ACTIVE_STREAMS:
                    ACTIVE_STREAMS[stream_id]["status"] = "cancelled"
                break
            chunk_end = min(current + 1024 * 1024 - 1, absolute_end)
            chunk = await reader.read_range(current, chunk_end)
            if not chunk:
                break
            current += len(chunk)
            _update_virtual_stream_stats(stream_id, len(chunk), last_measure)
            yield chunk

        await _finalize_virtual_stream(stream_id)
    except asyncio.CancelledError:
        if stream_id in ACTIVE_STREAMS:
            ACTIVE_STREAMS[stream_id]["status"] = "cancelled"
        await _finalize_virtual_stream(stream_id, status="cancelled")
        raise
    except Exception as e:
        if stream_id in ACTIVE_STREAMS:
            ACTIVE_STREAMS[stream_id]["status"] = "error"
            ACTIVE_STREAMS[stream_id]["error"] = str(e)
        LOGGER.exception("Virtual ZIP stored-entry stream failed: %s", e)
        await _finalize_virtual_stream(stream_id, status="error")
        raise


async def _tracked_zip_generator(request: Request, source, stream_id: str):
    last_measure = {"ts": time.time(), "bytes": 0}
    try:
        async for chunk in source:
            if await request.is_disconnected():
                if stream_id in ACTIVE_STREAMS:
                    ACTIVE_STREAMS[stream_id]["status"] = "cancelled"
                break
            _update_virtual_stream_stats(stream_id, len(chunk), last_measure)
            yield chunk
        await _finalize_virtual_stream(stream_id)
    except asyncio.CancelledError:
        if stream_id in ACTIVE_STREAMS:
            ACTIVE_STREAMS[stream_id]["status"] = "cancelled"
        await _finalize_virtual_stream(stream_id, status="cancelled")
        raise
    except Exception as e:
        if stream_id in ACTIVE_STREAMS:
            ACTIVE_STREAMS[stream_id]["status"] = "error"
            ACTIVE_STREAMS[stream_id]["error"] = str(e)
        LOGGER.exception("Virtual ZIP compressed-entry stream failed: %s", e)
        await _finalize_virtual_stream(stream_id, status="error")
        raise


async def _prefetched_virtual_file_range_generator(
    request: Request,
    reader,
    absolute_start: int,
    absolute_end: int,
    stream_id: str,
    http_chunk_size: int,
    prefetch: int,
):
    """Sequential stored-ZIP streamer with small read-ahead.

    The older virtual ZIP path fetched one 1 MiB Telegram block and then yielded it.
    VLC/Stremio often requests many 206 ranges, so the player can drain the server
    faster than Telegram returns the next block.  This producer/consumer keeps a
    few HTTP chunks ready without writing anything to disk.
    """
    last_measure = {"ts": time.time(), "bytes": 0}
    absolute_start = int(absolute_start)
    absolute_end = int(absolute_end)
    http_chunk_size = max(1024 * 1024, int(http_chunk_size or 4 * 1024 * 1024))
    queue_max = max(1, int(prefetch or 1))
    q: asyncio.Queue = asyncio.Queue(maxsize=queue_max)
    stop_event = asyncio.Event()

    async def producer():
        current = absolute_start
        try:
            while current <= absolute_end and not stop_event.is_set():
                chunk_end = min(current + http_chunk_size - 1, absolute_end)
                chunk = await reader.read_range(current, chunk_end)
                if not chunk:
                    break
                await q.put(chunk)
                current += len(chunk)
            await q.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("Virtual ZIP prefetch producer failed: %s", exc)
            try:
                await q.put(None)
            except Exception:
                pass

    producer_task = asyncio.create_task(producer())
    try:
        while True:
            if await request.is_disconnected():
                if stream_id in ACTIVE_STREAMS:
                    ACTIVE_STREAMS[stream_id]["status"] = "cancelled"
                break
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=90.0)
            except asyncio.TimeoutError:
                if stream_id in ACTIVE_STREAMS:
                    ACTIVE_STREAMS[stream_id]["status"] = "error"
                    ACTIVE_STREAMS[stream_id]["error"] = "Virtual ZIP producer stalled"
                break
            if chunk is None:
                break
            _update_virtual_stream_stats(stream_id, len(chunk), last_measure)
            yield chunk
        await _finalize_virtual_stream(stream_id)
    except asyncio.CancelledError:
        if stream_id in ACTIVE_STREAMS:
            ACTIVE_STREAMS[stream_id]["status"] = "cancelled"
        await _finalize_virtual_stream(stream_id, status="cancelled")
        raise
    except Exception as e:
        if stream_id in ACTIVE_STREAMS:
            ACTIVE_STREAMS[stream_id]["status"] = "error"
            ACTIVE_STREAMS[stream_id]["error"] = str(e)
        LOGGER.exception("Virtual ZIP stored-entry stream failed: %s", e)
        await _finalize_virtual_stream(stream_id, status="error")
        raise
    finally:
        stop_event.set()
        if not producer_task.done():
            producer_task.cancel()
            try:
                await asyncio.wait_for(producer_task, timeout=2.0)
            except (Exception, asyncio.CancelledError):
                pass




def _split_stream_cache_enabled() -> bool:
    return bool(getattr(Telegram, "SPLIT_STREAM_BLOCK_CACHE", True))


def _split_stream_cache_max_bytes() -> int:
    mb = int(getattr(Telegram, "SPLIT_STREAM_CACHE_MAX_MB", 1024) or 0)
    if mb <= 0:
        return 0
    return max(64, min(mb, 8192)) * 1024 * 1024


def _split_stream_cache_dir() -> Path:
    root = Path(getattr(Telegram, "SPLIT_ZIP_CACHE_DIR", "/tmp/tg_split_zip_cache") or "/tmp/tg_split_zip_cache")
    path = root / "range_blocks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _split_stream_cache_key(chat_id: int, msg_id: int, offset: int, limit: int) -> str:
    raw = f"{int(chat_id)}:{int(msg_id)}:{int(offset)}:{int(limit)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _split_stream_cache_path(cache_key: str) -> Path:
    d = _split_stream_cache_dir() / cache_key[:2]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{cache_key}.bin"


def _get_split_stream_block_lock(cache_key: str) -> asyncio.Lock:
    lock = _split_stream_block_locks.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _split_stream_block_locks[cache_key] = lock
    return lock


def _read_cached_split_stream_block(path: Path) -> bytes | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        data = path.read_bytes()
        if data:
            try:
                os.utime(path, None)
            except Exception:
                pass
            return data
    except Exception:
        return None
    return None


def _write_cached_split_stream_block(path: Path, data: bytes) -> None:
    if not data:
        return
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _prune_split_stream_cache_if_needed(force: bool = False) -> None:
    """Small LRU prune for split-stream block cache.

    This prevents repeated first/tail/seek ranges from re-downloading from
    Telegram, while keeping /tmp usage bounded for Hugging Face/Render.
    """
    global _split_stream_cache_last_prune
    max_bytes = _split_stream_cache_max_bytes()
    if max_bytes <= 0:
        return
    now = time.time()
    if not force and now - _split_stream_cache_last_prune < 60:
        return
    _split_stream_cache_last_prune = now
    try:
        root = _split_stream_cache_dir()
        files = [p for p in root.rglob("*.bin") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        if total <= max_bytes:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        target = int(max_bytes * 0.85)
        for p in files:
            if total <= target:
                break
            try:
                size = p.stat().st_size
                p.unlink(missing_ok=True)
                total -= size
            except Exception:
                pass
    except Exception as exc:
        LOGGER.debug("Split stream cache prune skipped: %s", exc)

async def _get_byte_streamer_for_client(client_index: int) -> ByteStreamer:
    tg_client = multi_clients.get(client_index)
    if tg_client is None:
        raise HTTPException(status_code=503, detail="Telegram client is not connected")
    if tg_client not in _streamer_by_client:
        _streamer_by_client[tg_client] = ByteStreamer(tg_client, client_index)
    return _streamer_by_client[tg_client]


async def _build_fast_part_session_pool(
    chat_id: int,
    msg_id: int,
    preferred_index: int | None = None,
    max_parallel: int | None = None,
):
    """Prepare exactly one Telegram session for a split-media playback.

    The previous router striped blocks between bot accounts. This stable route
    intentionally prepares one chosen client and lets other bots remain free
    for other viewers. ``max_parallel`` is retained for call compatibility.
    """
    if not multi_clients:
        raise HTTPException(status_code=503, detail="No Telegram clients are connected")

    ordered: list[int] = []
    if preferred_index in multi_clients:
        ordered.append(int(preferred_index))
    ordered.extend(idx for idx in _ordered_route_clients(0) if idx not in ordered)

    for c_idx in ordered:
        try:
            streamer = await _get_byte_streamer_for_client(c_idx)
            file_id = await asyncio.wait_for(
                streamer.get_file_properties(chat_id=chat_id, message_id=msg_id),
                timeout=10.0,
            )
            session = await asyncio.wait_for(streamer._get_media_session(file_id), timeout=12.0)
            slot = {
                "client_index": c_idx,
                "streamer": streamer,
                "file_id": file_id,
                "session": session,
                "location": await ByteStreamer._get_location(file_id),
                "chat_id": chat_id,
                "msg_id": msg_id,
            }
            LOGGER.debug("Split stream pinned msg=%s client=%s", msg_id, c_idx)
            return [slot], int(getattr(file_id, "file_size", 0) or 0), None
        except Exception as exc:
            client_failures[c_idx] = client_failures.get(c_idx, 0) + 1
            LOGGER.debug("Split stream client unavailable client=%s msg=%s: %s", c_idx, msg_id, exc)

    raise HTTPException(status_code=503, detail="Could not prepare a Telegram session for split media")


async def _prewarm_next_split_part(part: dict, preferred_index: int | None) -> None:
    """Warm the next split part on the same playback bot before a boundary."""
    try:
        chat_id = _telegram_chat_id_from_part(part)
        msg_id = int(part.get("msg_id"))
        await _build_fast_part_session_pool(
            chat_id=chat_id,
            msg_id=msg_id,
            preferred_index=preferred_index,
            max_parallel=1,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOGGER.debug("Split next-part prewarm skipped msg=%s: %s", part.get("msg_id"), exc)


async def _refresh_fast_part_location(slot: dict) -> bool:
    try:
        slot["streamer"]._file_id_cache.pop(int(slot["msg_id"]), None)
        fresh = await slot["streamer"].get_file_properties(chat_id=int(slot["chat_id"]), message_id=int(slot["msg_id"]))
        slot["file_id"] = fresh
        slot["location"] = await ByteStreamer._get_location(fresh)
        return True
    except Exception as exc:
        LOGGER.warning("Split ZIP fast location refresh failed msg=%s: %s", slot.get("msg_id"), exc)
        return False


async def _fast_telegram_message_range_generator(
    request: Request,
    chat_id: int,
    msg_id: int,
    local_start: int,
    local_end: int,
    stream_id: str,
    preferred_index: int | None = None,
):
    """Stream a byte range from one Telegram message using raw GetFile.

    This is low-RAM: only a small queue of 1 MiB chunks is held. It uses raw
    GetFile with limited same-bot prefetch, so each viewer remains on one bot
    while the overall pool remains available for other viewers.
    """
    from pyrogram import raw

    local_start = max(0, int(local_start))
    local_end = max(local_start, int(local_end))
    chunk_size = 1024 * 1024
    # The first byte takes priority. Later reads stay on the same assigned bot
    # with only a small same-bot read-ahead queue.
    _orig_range_for_split = request.headers.get("Range", "")
    fast_start = (not _orig_range_for_split) or _orig_range_for_split.replace(" ", "").lower().startswith("bytes=0-")
    parallelism, prefetch = _adaptive_stream_tuning(
        fast_start=fast_start,
        split_stream=True,
        current_registered=True,
    )
    parallelism = max(1, min(int(parallelism), 4))
    if fast_start:
        prefetch = max(2, min(3, prefetch))

    pool, file_size, warm_task = await _build_fast_part_session_pool(
        chat_id=chat_id,
        msg_id=msg_id,
        preferred_index=preferred_index,
        max_parallel=parallelism,
    )
    if file_size > 0:
        local_end = min(local_end, file_size - 1)

    offset = local_start - (local_start % chunk_size)
    first_cut = local_start - offset
    last_cut = (local_end % chunk_size) + 1
    part_count = max(1, ((local_end + 1 + chunk_size - 1) // chunk_size) - (offset // chunk_size))

    q: asyncio.Queue = asyncio.Queue(maxsize=max(1, prefetch))
    stop_event = asyncio.Event()
    last_measure = {"ts": time.time(), "bytes": ACTIVE_STREAMS.get(stream_id, {}).get("total_bytes", 0)}

    async def fetch_chunk(seq_idx: int, off: int):
        slot = _choose_fast_pool_slot(pool, seq_idx)
        cache_key = _split_stream_cache_key(int(chat_id), int(msg_id), int(off), int(chunk_size))
        cache_path = _split_stream_cache_path(cache_key) if _split_stream_cache_enabled() else None

        if cache_path is not None:
            cached = _read_cached_split_stream_block(cache_path)
            if cached is not None:
                return seq_idx, cached

        lock = _get_split_stream_block_lock(cache_key) if cache_path is not None else None
        if lock is not None:
            await lock.acquire()
            try:
                cached = _read_cached_split_stream_block(cache_path)
                if cached is not None:
                    return seq_idx, cached
                return await _fetch_chunk_from_telegram(seq_idx, off, slot, cache_path)
            finally:
                lock.release()
        return await _fetch_chunk_from_telegram(seq_idx, off, slot, cache_path)

    async def _fetch_chunk_from_telegram(seq_idx: int, off: int, slot: dict, cache_path: Path | None):
        tries = 0
        flood_tries = 0
        while tries < 3 and flood_tries < 5 and not stop_event.is_set():
            try:
                request_started = time.perf_counter()
                _mark_client_chunk_started(int(slot["client_index"]))
                try:
                    r = await asyncio.wait_for(
                        slot["session"].send(
                            raw.functions.upload.GetFile(
                                location=slot["location"],
                                offset=int(off),
                                limit=int(chunk_size),
                            )
                        ),
                        timeout=15.0,
                    )
                except Exception:
                    _mark_client_chunk_finished(
                        int(slot["client_index"]),
                        elapsed=time.perf_counter() - request_started,
                    )
                    raise
                data = bytes(getattr(r, "bytes", None) or b"")
                _mark_client_chunk_finished(
                    int(slot["client_index"]),
                    payload_size=len(data),
                    elapsed=time.perf_counter() - request_started,
                )
                if not data:
                    return seq_idx, None
                if cache_path is not None and data:
                    # Store full 1 MiB blocks and also the final short EOF block.
                    if len(data) >= chunk_size or int(off) + len(data) >= file_size:
                        _write_cached_split_stream_block(cache_path, data)
                        _prune_split_stream_cache_if_needed()
                return seq_idx, data
            except asyncio.TimeoutError:
                tries += 1
                client_failures[slot["client_index"]] = client_failures.get(slot["client_index"], 0) + 1
                await asyncio.sleep(min(0.5 * (2 ** (tries - 1)), 5.0))
            except Exception as exc:
                err = str(exc)
                if "FILE_REFERENCE" in err or "file_reference" in err.lower():
                    await _refresh_fast_part_location(slot)
                    tries += 1
                    continue
                flood_m = __import__("re").search(r"wait of (\d+) second", err, __import__("re").IGNORECASE)
                if flood_m:
                    flood_tries += 1
                    await asyncio.sleep(min(float(flood_m.group(1)) + 0.5, 30.0))
                    continue
                tries += 1
                client_failures[slot["client_index"]] = client_failures.get(slot["client_index"], 0) + 1
                LOGGER.warning("Split ZIP fast chunk error msg=%s off=%s try=%s: %s", msg_id, off, tries, err)
                await asyncio.sleep(min(0.5 * (2 ** (tries - 1)), 5.0))
        return seq_idx, None

    async def producer():
        next_to_schedule = 0
        next_to_put = 0
        tasks: dict[int, asyncio.Task] = {}
        results: dict[int, bytes] = {}

        def schedule_more():
            nonlocal next_to_schedule
            # One bot may handle a small number of in-flight GetFile reads.
            target_inflight = max(1, min(parallelism, part_count - next_to_put))
            while len(tasks) < target_inflight and next_to_schedule < part_count:
                seq = next_to_schedule
                tasks[seq] = asyncio.create_task(fetch_chunk(seq, offset + seq * chunk_size))
                next_to_schedule += 1

        try:
            # The first chunk is deliberately alone for instant playback.
            initial_parallelism = 1 if fast_start else max(1, min(parallelism, part_count))
            for _ in range(min(part_count, initial_parallelism)):
                seq = next_to_schedule
                tasks[seq] = asyncio.create_task(fetch_chunk(seq, offset + seq * chunk_size))
                next_to_schedule += 1

            while next_to_put < part_count and not stop_event.is_set():
                if not tasks:
                    schedule_more()
                done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    seq_key = next((key for key, task in tasks.items() if task is completed), None)
                    if seq_key is None:
                        continue
                    tasks.pop(seq_key, None)
                    seq_idx, data = completed.result()
                    if data is None:
                        await q.put(None)
                        return
                    results[seq_idx] = data

                while next_to_put in results:
                    await q.put((next_to_put, results.pop(next_to_put)))
                    next_to_put += 1
                schedule_more()
            await q.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("Split media router producer failed: %s", exc)
            try:
                await q.put(None)
            except Exception:
                pass
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()

    producer_task = asyncio.create_task(producer())
    try:
        while True:
            if await request.is_disconnected():
                if stream_id in ACTIVE_STREAMS:
                    ACTIVE_STREAMS[stream_id]["status"] = "cancelled"
                break
            item = await asyncio.wait_for(q.get(), timeout=90.0)
            if item is None:
                break
            seq_idx, chunk = item
            if part_count == 1:
                out = chunk[first_cut:last_cut]
            elif seq_idx == 0:
                out = chunk[first_cut:]
            elif seq_idx == part_count - 1:
                out = chunk[:last_cut]
            else:
                out = chunk
            if not out:
                continue
            _update_virtual_stream_stats(stream_id, len(out), last_measure)
            yield out
    finally:
        stop_event.set()
        if not producer_task.done():
            producer_task.cancel()
            try:
                await asyncio.wait_for(producer_task, timeout=2.0)
            except (Exception, asyncio.CancelledError):
                pass
        if warm_task is not None and not warm_task.done():
            warm_task.cancel()
            try:
                await asyncio.wait_for(warm_task, timeout=1.0)
            except (Exception, asyncio.CancelledError):
                pass


async def _fast_split_parts_range_generator(
    request: Request,
    parts: list,
    absolute_start: int,
    absolute_end: int,
    stream_id: str,
    preferred_index: int | None = None,
):
    """Stream a virtual archive byte range across split Telegram parts fast."""
    absolute_start = int(absolute_start)
    absolute_end = int(absolute_end)
    cursor = 0
    next_part_prewarm: asyncio.Task | None = None
    ordered_parts = sorted(parts, key=lambda p: int(p.get("part", 0)))
    try:
        for part_position, part in enumerate(ordered_parts):
            part_size = int(part.get("size_bytes") or part.get("size") or 0)
            if part_size <= 0:
                # If old DB rows do not have size_bytes, use the already resolved
                # Telegram message size via the fast generator later. For mapping
                # we must know sizes, so fail loudly with a useful message.
                raise HTTPException(status_code=422, detail="Split ZIP part size is missing. Re-scan/re-index the archive.")
            part_start = cursor
            part_end = cursor + part_size - 1
            cursor += part_size
            if part_end < absolute_start:
                continue
            if part_start > absolute_end:
                break
            seg_abs_start = max(absolute_start, part_start)
            seg_abs_end = min(absolute_end, part_end)
            local_start = seg_abs_start - part_start
            local_end = seg_abs_end - part_start
            # When this response crosses into the next Telegram part, begin
            # preparing that route now.  Do not wait here: current bytes remain
            # the playback priority.
            if part_end < absolute_end and part_position + 1 < len(ordered_parts):
                next_part = ordered_parts[part_position + 1]
                if next_part_prewarm is None or next_part_prewarm.done():
                    next_part_prewarm = asyncio.create_task(
                        _prewarm_next_split_part(next_part, preferred_index)
                    )
            chat_id = _telegram_chat_id_from_part(part)
            msg_id = int(part.get("msg_id"))
            async for data in _fast_telegram_message_range_generator(
                request=request,
                chat_id=chat_id,
                msg_id=msg_id,
                local_start=local_start,
                local_end=local_end,
                stream_id=stream_id,
                preferred_index=preferred_index,
            ):
                yield data
        await _finalize_virtual_stream(stream_id)
    except asyncio.CancelledError:
        if stream_id in ACTIVE_STREAMS:
            ACTIVE_STREAMS[stream_id]["status"] = "cancelled"
        await _finalize_virtual_stream(stream_id, status="cancelled")
        raise
    except Exception as exc:
        if stream_id in ACTIVE_STREAMS:
            ACTIVE_STREAMS[stream_id]["status"] = "error"
            ACTIVE_STREAMS[stream_id]["error"] = str(exc)
        LOGGER.exception("Fast split ZIP stored stream failed: %s", exc)
        await _finalize_virtual_stream(stream_id, status="error")
        raise
    finally:
        if next_part_prewarm is not None and not next_part_prewarm.done():
            next_part_prewarm.cancel()
            try:
                await asyncio.wait_for(next_part_prewarm, timeout=1.0)
            except (Exception, asyncio.CancelledError):
                pass


def _split_file_headers(file_name: str, mime_type: str, req_length: int, range_header: str,
                        start: int, end: int, file_size: int) -> dict:
    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(req_length),
        "Content-Disposition": f'inline; filename="{file_name}"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600, no-transform",
        "X-Archive-Mode": "virtual-telegram-split-file",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag, X-Resume-Support, X-Archive-Mode, X-Player-Window, X-Stream-Router",
    }
    headers.update(_player_resume_headers(file_size, file_name))
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    return headers


async def split_file_media_streamer(
    request: Request,
    decoded: dict,
    token: str,
    token_data: dict = None,
    stream_id_hash: str = None,
):
    """Stream direct split video parts like Movie.mkv.001 + .002 as one file.

    This is faster and smoother than ZIP because the player byte range maps
    directly to Telegram message ranges. Nothing is joined on disk and each
    HTTP response is capped to a Render-safe 50 MB window by default.
    """
    from fastapi.responses import Response as PlainResponse

    parts = decoded.get("parts") or []
    if not parts:
        raise HTTPException(status_code=400, detail="Split video stream has no parts")

    parts = sorted(parts, key=lambda p: int(p.get("part", 0)))
    part_numbers = [int(p.get("part", 0)) for p in parts]
    expected = list(range(1, len(parts) + 1))
    if part_numbers != expected:
        raise HTTPException(
            status_code=409,
            detail=f"Split video parts are incomplete. Found {part_numbers}, expected {expected}",
        )

    total_size = sum(int(p.get("size_bytes") or p.get("size") or 0) for p in parts)
    if total_size <= 0:
        raise HTTPException(status_code=422, detail="Split video part sizes are missing. Re-scan/re-index the parts.")

    if not multi_clients:
        raise HTTPException(status_code=503, detail="No Telegram clients are connected")

    file_name = Path(decoded.get("file_name") or decoded.get("title") or "video.mkv").name
    affinity_key = _stream_viewer_key(request, token, stream_id_hash or f"split-file:{file_name}")
    client_index, router_mode = _select_affinity_client(affinity_key, 0)
    mime_type = mimetypes.guess_type(file_name)[0] or "video/x-matroska"

    original_range_header = request.headers.get("Range", "")
    range_header = original_range_header
    capped_player_window = False
    forced_initial_range = False
    if request.method != "HEAD":
        range_header, capped_player_window = _cap_split_zip_player_range(range_header, total_size)
        forced_initial_range = not bool(original_range_header) and bool(range_header)

    start, end = parse_range_header(range_header, total_size)
    req_length = end - start + 1
    headers = _split_file_headers(
        file_name=file_name,
        mime_type=mime_type,
        req_length=req_length,
        range_header=range_header,
        start=start,
        end=end,
        file_size=total_size,
    )
    if forced_initial_range:
        headers["X-Forced-Initial-Range"] = "true"
    if capped_player_window:
        headers["X-Player-Window"] = _split_stream_window_label()
    headers["X-Stream-Router"] = f"{router_mode}; client={client_index}; policy=one-stream-one-bot"

    status_code = 206 if range_header else 200
    if request.method == "HEAD":
        return PlainResponse(status_code=status_code, headers=headers)

    stream_id = secrets.token_hex(8)
    title = decoded.get("title") or file_name
    meta = {
        "request_path": str(request.url.path),
        "client_host": request.client.host if request.client else None,
        "title": title,
        "user_name": token_data.get("name", "Unknown") if token_data else "Unknown",
        "source_type": "split_file_virtual",
        "file_name": file_name,
        "part_count": len(parts),
        "forced_initial_range": forced_initial_range,
        "player_window": _split_stream_window_label() if capped_player_window else None,
        "note": "Direct split video parts map byte ranges directly and are the recommended low-buffer method.",
    }
    _register_virtual_stream(stream_id, decoded, meta, client_index, parts)
    asyncio.create_task(track_usage_from_stats(stream_id, token, token_data))

    LOGGER.debug(
        "Virtual split video stream: %s parts=%s range=%s-%s/%s forced_initial=%s window=%s",
        file_name,
        len(parts),
        start,
        end,
        total_size,
        forced_initial_range,
        _split_stream_window_label() if capped_player_window else "client",
    )

    generator = _fast_split_parts_range_generator(
        request=request,
        parts=parts,
        absolute_start=start,
        absolute_end=end,
        stream_id=stream_id,
        preferred_index=client_index,
    )

    return StreamingResponse(
        generator,
        headers=headers,
        status_code=status_code,
        media_type=mime_type,
    )


async def split_zip_media_streamer(
    request: Request,
    decoded: dict,
    token: str,
    token_data: dict = None,
    stream_id_hash: str = None,
):
    """Stream a video inside split ZIP parts using low-RAM virtual ZIP reads."""
    if not getattr(Telegram, "SPLIT_ZIP_STREAM", True):
        raise HTTPException(status_code=403, detail="Split ZIP streaming is disabled")

    from fastapi.responses import Response as PlainResponse
    from Backend.helper.split_archive import (
        TelegramSeekableReader,
        get_zip_entry_data_offset,
        list_zip_files_sync,
        zip_compressed_generator,
    )
    import anyio
    import zipfile as _zipfile

    affinity_key = _stream_viewer_key(request, token, stream_id_hash or f"split-zip:{decoded.get('archive_name') or decoded.get('title') or ''}")
    preferred_index, router_mode = _select_affinity_client(affinity_key, 0)
    client_index, tg_client, messages, parts = await _resolve_split_zip_messages(decoded, preferred_index=preferred_index)
    if client_index != preferred_index:
        _release_stream_affinity(affinity_key)
    reader = TelegramSeekableReader(
        tg_client,
        messages,
        block_size=1024 * 1024,
        max_cached_blocks=int(getattr(Telegram, "VIRTUAL_ZIP_MAX_CACHED_BLOCKS", 16)),
    )
    if reader.total_size < 4:
        raise HTTPException(status_code=422, detail="Split ZIP archive is empty")

    cache_key = _split_zip_meta_cache_key(decoded, parts)
    meta_cache = _get_cached_split_zip_meta(cache_key)
    cache_status = "hit" if meta_cache else "miss"

    if meta_cache:
        entry_filename = meta_cache["filename"]
        file_name = Path(entry_filename).name or "video.mkv"
        file_size = int(meta_cache["file_size"])
        compress_type = int(meta_cache["compress_type"])
        header_offset = int(meta_cache.get("header_offset", 0))
        data_offset = int(meta_cache.get("data_offset", 0)) if meta_cache.get("data_offset") is not None else None
    else:
        first_block = await reader.fetch_block(0, 0)
        if not first_block.startswith(b"PK\x03\x04"):
            raise HTTPException(status_code=422, detail="Split ZIP first part is not a valid ZIP archive")

        loop = asyncio.get_running_loop()
        entries = await anyio.to_thread.run_sync(list_zip_files_sync, reader, loop)
        entry = _pick_zip_video_entry(entries, decoded.get("zip_entry") or decoded.get("entry_name"))

        entry_filename = entry.filename
        file_name = Path(entry.filename).name or "video.mkv"
        file_size = int(entry.file_size or 0)
        compress_type = int(entry.compress_type)
        header_offset = int(entry.header_offset)
        data_offset = None
        if compress_type == _zipfile.ZIP_STORED:
            data_offset = await get_zip_entry_data_offset(reader, header_offset)

        _set_cached_split_zip_meta(cache_key, {
            "filename": entry_filename,
            "file_size": file_size,
            "compress_type": compress_type,
            "header_offset": header_offset,
            "data_offset": data_offset,
        })

    if file_size <= 0:
        raise HTTPException(status_code=422, detail="ZIP video entry has invalid size")

    mime_type = mimetypes.guess_type(file_name)[0] or "video/x-matroska"
    original_range_header = request.headers.get("Range", "")
    range_header = original_range_header
    forced_initial_range = False

    capped_player_window = False
    if request.method != "HEAD" and getattr(Telegram, "VIRTUAL_ZIP_FORCE_RANGE", True):
        # Give Stremio/VLC a useful 50 MB playback window instead of a tiny
        # first probe or one huge multi-GB response. This keeps playback smooth
        # while staying safer for low-RAM Render/Koyeb-style hosts.
        range_header, capped_player_window = _cap_split_zip_player_range(range_header, file_size)
        forced_initial_range = not bool(original_range_header) and bool(range_header)

    start, end = parse_range_header(range_header, file_size)
    req_length = end - start + 1
    compression = _zip_compression_label(compress_type)
    headers = _split_zip_headers(
        file_name=file_name,
        mime_type=mime_type,
        req_length=req_length,
        range_header=range_header,
        start=start,
        end=end,
        file_size=file_size,
        compression=compression,
        cache_status=cache_status,
    )
    if forced_initial_range:
        headers["X-Forced-Initial-Range"] = "true"
    if capped_player_window:
        headers["X-Player-Window"] = _split_stream_window_label()
    headers["X-Stream-Router"] = f"{router_mode}; client={client_index}; policy=one-stream-one-bot"

    status_code = 206 if range_header else 200

    if request.method == "HEAD":
        return PlainResponse(status_code=status_code, headers=headers)

    stream_id = secrets.token_hex(8)
    title = decoded.get("title") or decoded.get("archive_name") or file_name
    meta = {
        "request_path": str(request.url.path),
        "client_host": request.client.host if request.client else None,
        "title": title,
        "user_name": token_data.get("name", "Unknown") if token_data else "Unknown",
        "source_type": "split_zip_virtual",
        "archive_name": decoded.get("archive_name"),
        "zip_entry": entry_filename,
        "compress_type": compress_type,
        "compression": compression,
        "meta_cache": cache_status,
        "forced_initial_range": forced_initial_range,
        "player_window": _split_stream_window_label() if capped_player_window else None,
        "note": "Stored/no-compression ZIP entries seek best. Compressed ZIP seeking is slow by ZIP design.",
    }
    _register_virtual_stream(stream_id, decoded, meta, client_index, parts)
    asyncio.create_task(track_usage_from_stats(stream_id, token, token_data))

    LOGGER.debug(
        "Virtual split ZIP stream: %s entry=%s compression=%s cache=%s range=%s-%s/%s forced_initial=%s window=%s",
        decoded.get("archive_name") or title,
        entry_filename,
        compression,
        cache_status,
        start,
        end,
        file_size,
        forced_initial_range,
        _split_stream_window_label() if capped_player_window else "client",
    )

    if compress_type == _zipfile.ZIP_STORED:
        if data_offset is None:
            data_offset = await get_zip_entry_data_offset(reader, header_offset)
        # Fast path: stored/no-compression ZIP entries are just raw bytes inside
        # the split archive. Stream those bytes through the same raw Telegram
        # GetFile/prefetch logic used by normal direct videos. This greatly
        # reduces buffering compared with stream_media()-based virtual reads.
        generator = _fast_split_parts_range_generator(
            request=request,
            parts=parts,
            absolute_start=data_offset + start,
            absolute_end=data_offset + end,
            stream_id=stream_id,
            preferred_index=client_index,
        )
    else:
        # Cannot truly seek fast inside compressed ZIP. This path is kept to make
        # compressed archives playable, but the correct fix for buffering is to
        # recreate the ZIP with -mx=0 or split the raw .mkv directly.
        LOGGER.warning(
            "Split ZIP entry is compressed (%s): buffering/slow seek is expected. "
            "Recreate the archive with 7z -tzip -mx=0 or upload split .mkv parts for smooth playback.",
            compression,
        )
        generator = _tracked_zip_generator(
            request,
            zip_compressed_generator(reader, entry_filename, start, end),
            stream_id,
        )

    return StreamingResponse(
        generator,
        headers=headers,
        status_code=status_code,
        media_type=mime_type,
    )


@router.get("/dl/{token}/{id}/{name}")
@router.head("/dl/{token}/{id}/{name}")
async def stream_handler(
    request: Request,
    token: str,
    id: str,
    name: str,
    token_data: dict = Depends(verify_token),
):
    decoded = await decode_string(id)

    if decoded.get("type") == "split_zip":
        return await split_zip_media_streamer(
            request=request,
            decoded=decoded,
            token=token,
            token_data=token_data,
            stream_id_hash=id,
        )

    if decoded.get("type") == "split_file":
        return await split_file_media_streamer(
            request=request,
            decoded=decoded,
            token=token,
            token_data=token_data,
            stream_id_hash=id,
        )

    msg_id = decoded.get("msg_id")
    if not msg_id:
        raise HTTPException(status_code=400, detail="Missing id")

    chat_id = int(f"-100{decoded['chat_id']}")
    # Token already authenticates the request; the hash check inside
    # media_streamer is skipped to avoid an extra get_messages() round-trip
    # on every seek.  File identity is verified by the streaming client.
    return await media_streamer(
        request=request,
        chat_id=chat_id,
        msg_id=int(msg_id),
        secure_hash="SKIP_HASH_CHECK",
        token=token,
        token_data=token_data,
        stream_id_hash=id,
    )

async def media_streamer(
    request: Request,
    chat_id: int,
    msg_id: int,
    secure_hash: str,
    token: str,
    token_data: dict = None,
    stream_id_hash: str = None,
):
    # Reuse the existing affinity route before probing. That means playback,
    # resume and seeking do not touch a different bot between HTTP ranges.
    affinity_key = _stream_viewer_key(request, token, stream_id_hash or f"{chat_id}:{msg_id}")
    _prune_stream_affinities()
    existing_route = _stream_affinities.get(affinity_key)
    if existing_route and existing_route[0] in multi_clients and int(client_failures.get(existing_route[0], 0) or 0) < 4:
        probe_index = int(existing_route[0])
        router_mode = "sticky"
        _stream_affinities[affinity_key] = (probe_index, time.time() + _stream_affinity_seconds())
    else:
        _release_stream_affinity(affinity_key)
        probe_index = select_best_client(0)
        router_mode = "new"

    tg_client = multi_clients[probe_index]
    if tg_client not in _streamer_by_client:
        _streamer_by_client[tg_client] = ByteStreamer(tg_client, probe_index)
    streamer: ByteStreamer = _streamer_by_client[tg_client]

    file_id = await streamer.get_file_properties(chat_id=chat_id, message_id=msg_id)
    target_dc = int(getattr(file_id, "dc_id", 0) or 0)

    if secure_hash != "SKIP_HASH_CHECK" and file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    if router_mode == "sticky":
        index = probe_index
    else:
        index, router_mode = _select_affinity_client(affinity_key, target_dc)

    if index != probe_index and index in multi_clients:
        routed_client = multi_clients[index]
        if routed_client not in _streamer_by_client:
            _streamer_by_client[routed_client] = ByteStreamer(routed_client, index)
        routed_streamer: ByteStreamer = _streamer_by_client[routed_client]
        try:
            routed_file_id = await asyncio.wait_for(
                routed_streamer.get_file_properties(chat_id=chat_id, message_id=msg_id),
                timeout=4.0,
            )
            file_id = routed_file_id
            streamer = routed_streamer
            tg_client = routed_client
        except Exception as exc:
            client_failures[index] = client_failures.get(index, 0) + 1
            _release_stream_affinity(affinity_key)
            LOGGER.debug(
                "Sticky stream route fallback: client=%s probe=%s target_dc=%s reason=%s",
                index, probe_index, target_dc, exc,
            )
            index = probe_index
            router_mode = "probe-fallback"
            _stream_affinities[affinity_key] = (index, time.time() + _stream_affinity_seconds())

    LOGGER.debug(
        "Stream router msg_id=%s target_dc=%s probe_client=%s stream_client=%s mode=%s",
        msg_id, target_dc, probe_index, index, router_mode,
    )

    file_size = file_id.file_size
    original_range_header = request.headers.get("Range", "")
    range_header = original_range_header
    forced_initial_range = False
    capped_player_window = False
    if request.method != "HEAD" and getattr(Telegram, "STREAM_FORCE_RANGE", True):
        range_header, capped_player_window = _cap_player_range(
            range_header,
            file_size,
            int(getattr(Telegram, "STREAM_INITIAL_RANGE_MB", 32) or 32) * 1024 * 1024,
            int(getattr(Telegram, "STREAM_SEEK_WINDOW_MB", 16) or 16) * 1024 * 1024,
        )
        forced_initial_range = not bool(original_range_header) and bool(range_header)

    start, end = parse_range_header(range_header, file_size)
    req_length = end - start + 1

    # Adaptive chunk size based on this client's recent measured throughput
    chunk_size = get_adaptive_chunk_size(index)
    offset = start - (start % chunk_size)
    first_part_cut = start - offset
    last_part_cut = (end % chunk_size) + 1
    part_count = max(1, (end // chunk_size) - (offset // chunk_size) + 1)
    fast_start = _is_fast_start_request(original_range_header, start)

    from urllib.parse import unquote
    
    stream_id = secrets.token_hex(8)
    
    # Extract original title from the URL path name, fallback to raw name
    decoded_name = unquote(request.path_params.get("name", ""))
    
    # Look up the real title — cached to avoid a DB hit on every seek.
    db_title = None
    if stream_id_hash:
        _now = time.time()
        _cached = _title_cache.get(stream_id_hash)
        if _cached and _now < _cached[1]:
            db_title = _cached[0]
        else:
            db_title = await db.get_title_by_stream_id(stream_id_hash)
            _title_cache[stream_id_hash] = (db_title, _now + _TITLE_CACHE_TTL)
            LOGGER.info(f"Stream lookup for hash '{stream_id_hash}' returned title: {db_title}")
        
    final_title = db_title if db_title else decoded_name
    
    meta = {
        "request_path": str(request.url.path),
        "client_host": request.client.host if request.client else None,
        "title": final_title,
        "user_name": token_data.get("name", "Unknown") if token_data else "Unknown",
        "range_start": start,
        "range_end": end,
        "router_mode": router_mode,
        "target_dc": target_dc,
        "stream_client": index,
        "forced_initial_range": forced_initial_range,
        "player_window": f"{int(getattr(Telegram, 'STREAM_INITIAL_RANGE_MB', 32) or 32)}mb" if capped_player_window else None,
    }

    # Parallelism and prefetch scale automatically with active viewers and
    # available Telegram clients.  Fast-start requests stay intentionally light.
    parallelism, prefetch_count = _adaptive_stream_tuning(
        fast_start=fast_start,
        split_stream=False,
        current_registered=False,
    )
    meta["parallelism"] = parallelism
    meta["prefetch"] = prefetch_count
    meta["tuning"] = "adaptive"

    # Deliberately do not add extra bot sessions here. One playback stays on
    # ``index``; the router distributes other viewers to the other bots.
    extra_clients_for_stream = None

    body_gen = await streamer.prefetch_stream(
        file_id=file_id,
        client_index=index,
        offset=offset,
        first_part_cut=first_part_cut,
        last_part_cut=last_part_cut,
        part_count=part_count,
        chunk_size=chunk_size,
        prefetch=prefetch_count,
        stream_id=stream_id,
        meta=meta,
        parallelism=parallelism,
        request=request,
        chat_id=chat_id,
        message_id=msg_id,
        extra_clients=extra_clients_for_stream,
    )

    asyncio.create_task(track_usage_from_stats(stream_id, token, token_data))

    file_name = file_id.file_name or f"{secrets.token_hex(4)}.bin"
    mime_type = file_id.mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    if "." not in file_name and "/" in mime_type:
        file_name = f"{file_name}.{mime_type.split('/')[1]}"

    # HEAD request support
    from fastapi.responses import Response as PlainResponse

    if request.method == "HEAD":
        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(req_length),
            "Content-Disposition": f'inline; filename="{file_name}"',
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600, no-transform",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag, X-Resume-Support, X-Player-Window, X-Stream-Router",
            "X-Stream-Router": f"{router_mode}; dc={target_dc}; client={index}; policy=one-stream-one-bot",
        }
        headers.update(_player_resume_headers(file_size, f"{chat_id}:{msg_id}:{stream_id_hash or ''}"))
        if capped_player_window:
            headers["X-Player-Window"] = f"{int(getattr(Telegram, 'STREAM_INITIAL_RANGE_MB', 32) or 32)}mb"

        if range_header:
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

        return PlainResponse(
            status_code=206 if range_header else 200,
            headers=headers,
        )

    headers = {
        "Content-Type": mime_type,
        "Content-Disposition": f'inline; filename="{file_name}"',
        "Accept-Ranges": "bytes",
        "Content-Length": str(req_length),
        "Cache-Control": "public, max-age=3600, no-transform",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, ETag, X-Resume-Support, X-Player-Window, X-Stream-Router",
        "X-Stream-Router": f"{router_mode}; dc={target_dc}; client={index}; policy=one-stream-one-bot",
    }
    headers.update(_player_resume_headers(file_size, f"{chat_id}:{msg_id}:{stream_id_hash or ''}"))
    if forced_initial_range:
        headers["X-Forced-Initial-Range"] = "true"
    if capped_player_window:
        headers["X-Player-Window"] = f"{int(getattr(Telegram, 'STREAM_INITIAL_RANGE_MB', 32) or 32)}mb"

    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        status = 206
    else:
        status = 200

    return StreamingResponse(
        body_gen,
        headers=headers,
        status_code=status,
        media_type=mime_type,
    )


@router.get("/stream/stats")
async def get_stream_stats():
    now = time.time()

    PRUNE_SECONDS = 3
    INACTIVE_TIMEOUT = 15  # 15 sec no data = inactive

    for sid, info in list(ACTIVE_STREAMS.items()):
        status = info.get("status", "active")

        current_bytes = info.get("total_bytes", 0)

        if "last_bytes" not in info:
            info["last_bytes"] = current_bytes
            info["last_activity_ts"] = now

        
        if current_bytes > info["last_bytes"]:
            # Data is flowing → update activity timestamp
            info["last_bytes"] = current_bytes
            info["last_activity_ts"] = now
            info["status"] = "active"  # ensure it stays active if resumed
        else:
            # No data flow → check inactivity timeout
            if now - info["last_activity_ts"] > INACTIVE_TIMEOUT:
                if status == "active":
                    info["status"] = "cancelled"
                    info["end_ts"] = now
                    
        if info.get("status") in ("cancelled", "error", "finished", "inactive"):
            last_ts = info.get("end_ts", info.get("last_activity_ts", now))
            if now - last_ts > PRUNE_SECONDS:
                try:
                    RECENT_STREAMS.appendleft(ACTIVE_STREAMS.pop(sid))
                except KeyError:
                    pass

    active = []
    for sid, info in ACTIVE_STREAMS.items():
        active.append(
            {
                "stream_id": sid,
                "msg_id": info.get("msg_id"),
                "chat_id": info.get("chat_id"),
                "title": info.get("meta", {}).get("title"),
                "client_index": info.get("client_index"),
                "dc_id": info.get("dc_id"),
                "router_mode": info.get("meta", {}).get("router_mode"),
                "target_dc": info.get("meta", {}).get("target_dc"),
                "status": info.get("status"),
                "total_bytes": info.get("total_bytes"),
                "instant_mbps": round(info.get("instant_mbps", 0.0), 3),
                "avg_mbps": round(info.get("avg_mbps", 0.0), 3),
                "peak_mbps": round(info.get("peak_mbps", 0.0), 3),
                "start_ts": info.get("start_ts"),
            }
        )

    recent = []
    for info in RECENT_STREAMS:
        recent.append(
            {
                "stream_id": info.get("stream_id"),
                "msg_id": info.get("msg_id"),
                "chat_id": info.get("chat_id"),
                "title": info.get("meta", {}).get("title"),
                "client_index": info.get("client_index"),
                "dc_id": info.get("dc_id"),
                "router_mode": info.get("meta", {}).get("router_mode"),
                "target_dc": info.get("meta", {}).get("target_dc"),
                "status": info.get("status"),
                "total_bytes": info.get("total_bytes"),
                "duration": info.get("duration"),
                "avg_mbps": round(info.get("avg_mbps", 0.0), 3),
                "start_ts": info.get("start_ts"),
                "end_ts": info.get("end_ts"),
            }
        )

    return JSONResponse(
        {
            "active_streams": active,
            "recent_streams": recent,
            "router": {
                "enabled": True,
                "mode": "smart-dc-aware",
                "same_dc_first": True,
                "uses_load": True,
                "uses_failures": True,
                "uses_speed_history": True,
            },
            "client_dc_map": client_dc_map,
            "work_loads": work_loads,
            "client_failures": client_failures,
            "client_avg_mbps": client_avg_mbps,
        }
    )

@router.get("/stream/stats/{stream_id}")
async def get_stream_detail(stream_id: str):
    info = ACTIVE_STREAMS.get(stream_id)
    if info:
        return JSONResponse(make_json_safe(info))

    for rec in RECENT_STREAMS:
        if rec.get("stream_id") == stream_id:
            return JSONResponse(make_json_safe(rec))

    raise HTTPException(status_code=404, detail="Stream not found")


# ─────────────────────────────────────────────────────────────
# Subtitle serving route
# /sub/{token}/{subtitle_id}/subtitle.vtt
# Streams the raw subtitle file from Telegram and converts
# SRT → VTT on-the-fly so Stremio can display it.
# ─────────────────────────────────────────────────────────────

def _srt_to_vtt(srt_bytes: bytes) -> bytes:
    """Convert SRT subtitle bytes to WebVTT format."""
    try:
        text = srt_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = srt_bytes.decode("latin-1", errors="replace")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = ["WEBVTT", ""]
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Skip cue-number lines (pure digits)
        if line.isdigit():
            i += 1
            continue
        # Timestamp line: convert "," decimal separator → "."
        if "-->" in line:
            out.append(line.replace(",", "."))
            i += 1
            # Copy text lines until blank line
            while i < len(lines) and lines[i].strip() != "":
                out.append(lines[i].rstrip())
                i += 1
            out.append("")
        else:
            i += 1
    return "\n".join(out).encode("utf-8")


def _ass_to_vtt(ass_bytes: bytes) -> bytes:
    """Strip ASS/SSA formatting and convert to WebVTT."""
    import re as _re
    try:
        text = ass_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = ass_bytes.decode("latin-1", errors="replace")

    out = ["WEBVTT", ""]
    cue_idx = 1
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        start_raw = parts[1].strip()
        end_raw   = parts[2].strip()
        content   = parts[9].strip()
        # Remove ASS override tags {…} and \N line breaks
        content = _re.sub(r"\{[^}]*\}", "", content).replace(r"\N", "\n").replace(r"\n", "\n")
        # Convert H:MM:SS.cc → HH:MM:SS.ccc
        def _ts(t):
            h, m, s = t.split(":")
            s_int, cs = s.split(".")
            return f"{int(h):02d}:{int(m):02d}:{int(s_int):02d}.{int(cs)*10:03d}"
        try:
            out.append(f"{cue_idx}")
            out.append(f"{_ts(start_raw)} --> {_ts(end_raw)}")
            out.append(content)
            out.append("")
            cue_idx += 1
        except Exception:
            continue
    return "\n".join(out).encode("utf-8")


@router.get("/sub/{token}/{sub_id}/{filename}")
@router.head("/sub/{token}/{sub_id}/{filename}")
async def subtitle_handler(
    token: str,
    sub_id: str,
    filename: str,
    request: Request,
    token_data: dict = Depends(verify_token),
):
    """Serve a subtitle file from Telegram or a public URL, converting to VTT for Stremio."""
    # Decode the subtitle ID
    try:
        decoded = await decode_string(sub_id)
    except (InvalidHash, KeyError, ValueError):
        raise HTTPException(status_code=404, detail="Invalid subtitle ID")

    # ── URL-based subtitle ────────────────────────────────────
    if decoded.get("source_type") == "url" or "url" in decoded:
        import httpx
        ext_url = decoded["url"]

        # Look up subtitle record to get original format
        sub_doc = await db.get_subtitle_by_id(sub_id)
        fmt = sub_doc.get("format", "srt").lower() if sub_doc else "srt"

        if request.method == "HEAD":
            return StreamingResponse(iter([]), media_type="text/vtt",
                                     headers={"Content-Type": "text/vtt; charset=utf-8"})
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                r = await client.get(ext_url)
                r.raise_for_status()
                raw_bytes = r.content
        except Exception as e:
            LOGGER.error(f"[SubtitleHandler] Failed to fetch URL subtitle: {e}")
            raise HTTPException(status_code=502, detail="Could not fetch subtitle from URL")

        vtt_bytes = raw_bytes if fmt == "vtt" else (
            _ass_to_vtt(raw_bytes) if fmt in ("ass", "ssa") else _srt_to_vtt(raw_bytes))
        return StreamingResponse(iter([vtt_bytes]), media_type="text/vtt",
                                 headers={"Content-Type": "text/vtt; charset=utf-8",
                                          "Content-Length": str(len(vtt_bytes)),
                                          "Cache-Control": "public, max-age=86400",
                                          "Access-Control-Allow-Origin": "*"})

    # ── Telegram-based subtitle ───────────────────────────────
    try:
        chat_id = int(decoded["chat_id"])
        msg_id  = int(decoded["msg_id"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="Invalid subtitle ID")

    # Look up subtitle record to get original format
    sub_doc = await db.get_subtitle_by_id(sub_id)
    if not sub_doc:
        raise HTTPException(status_code=404, detail="Subtitle not found")

    fmt = sub_doc.get("format", "srt").lower()

    # HEAD request — confirm existence only
    if request.method == "HEAD":
        return StreamingResponse(
            iter([]),
            media_type="text/vtt",
            headers={"Content-Type": "text/vtt; charset=utf-8"},
        )

    # Reconstruct the Telegram channel chat_id (re-add -100 prefix)
    tg_chat_id = int(f"-100{chat_id}")

    # Use first available client — subtitles are tiny, DC doesn't matter
    tg_client = multi_clients.get(0) or next(iter(multi_clients.values()))

    # Fetch the Telegram message and download to memory
    try:
        message = await tg_client.get_messages(tg_chat_id, msg_id)
        if not message or not message.document:
            raise HTTPException(status_code=404, detail="Subtitle message not found in Telegram")

        raw_bytes = await tg_client.download_media(message, in_memory=True)
        if hasattr(raw_bytes, "getvalue"):
            raw_bytes = raw_bytes.getvalue()
        raw_bytes = bytes(raw_bytes)
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"[SubtitleHandler] Failed to download subtitle {sub_id}: {e}")
        raise HTTPException(status_code=502, detail="Could not fetch subtitle from Telegram")

    # Convert to VTT
    if fmt == "vtt":
        vtt_bytes = raw_bytes
    elif fmt in ("ass", "ssa"):
        vtt_bytes = _ass_to_vtt(raw_bytes)
    else:
        # Default: treat as SRT
        vtt_bytes = _srt_to_vtt(raw_bytes)

    return StreamingResponse(
        iter([vtt_bytes]),
        media_type="text/vtt",
        headers={
            "Content-Type": "text/vtt; charset=utf-8",
            "Content-Length": str(len(vtt_bytes)),
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        },
    )
