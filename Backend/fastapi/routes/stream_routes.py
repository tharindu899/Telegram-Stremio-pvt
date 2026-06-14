import math
import secrets
import mimetypes
import time
from typing import Dict

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse

from collections import deque

from Backend import db
from Backend.helper.encrypt import decode_string
from Backend.helper.exceptions import InvalidHash
from Backend.helper.custom_dl import ByteStreamer, ACTIVE_STREAMS, RECENT_STREAMS, get_adaptive_chunk_size
from Backend.pyrofork.bot import work_loads, multi_clients, client_dc_map, client_failures, client_avg_mbps
from Backend.config import Telegram
from Backend.logger import LOGGER
from Backend.fastapi.security.tokens import verify_token
import asyncio

router = APIRouter(tags=["Streaming"])

_streamer_by_client: Dict = {}
_rr_counter: int = 0

_title_cache: Dict[str, tuple] = {}
_TITLE_CACHE_TTL = 300


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


def select_best_client(target_dc: int) -> int:
    """Pick the best available client with round-robin tie-breaking.

    Score = work_loads + 3 × client_failures.
    When multiple clients share the minimum score (typical at startup and
    between short seeks) Python's min() always returns the first key — i.e.
    client 0 for every request.  The round-robin counter breaks that tie so
    all 16 bots share the load and no single bot hits Telegram FLOOD_WAIT.

    target_dc > 0  →  prefer same-DC clients; fall back to all if none match.
    target_dc == 0 →  no DC preference; pool is all available clients.
    """
    global _rr_counter

    def _score(idx: int) -> int:
        return work_loads.get(idx, 0) + 3 * client_failures.get(idx, 0)

    if target_dc > 0:
        matching = [
            idx for idx, dc in client_dc_map.items()
            if dc == target_dc and idx in multi_clients
        ]
    else:
        matching = []

    if not matching:
        matching = list(multi_clients.keys())
    if not matching:
        return 0

    min_score = min(_score(i) for i in matching)
    tied = sorted(i for i in matching if _score(i) == min_score)

    # Round-robin among equally-scored candidates so every bot gets used.
    selected = tied[_rr_counter % len(tied)]
    _rr_counter = (_rr_counter + 1) % max(len(multi_clients), 1)

    LOGGER.debug(
        "select_best_client DC=%s → client=%s (score=%s, pool=%s)",
        target_dc, selected, min_score, len(tied),
    )
    return selected


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
    # Pick the primary client and fetch file metadata.
    index = select_best_client(0)
    tg_client = multi_clients[index]
    if tg_client not in _streamer_by_client:
        _streamer_by_client[tg_client] = ByteStreamer(tg_client, index)
    streamer: ByteStreamer = _streamer_by_client[tg_client]

    file_id = await streamer.get_file_properties(chat_id=chat_id, message_id=msg_id)
    target_dc = file_id.dc_id

    if secure_hash != "SKIP_HASH_CHECK":
        if file_id.unique_id[:6] != secure_hash:
            raise InvalidHash

    LOGGER.debug("Stream msg_id=%s DC=%s via client=%s", msg_id, target_dc, index)

    file_size = file_id.file_size
    range_header = request.headers.get("Range", "")
    start, end = parse_range_header(range_header, file_size)
    req_length = end - start + 1

    # Adaptive chunk size based on this client's recent measured throughput
    chunk_size = get_adaptive_chunk_size(index)
    offset = start - (start % chunk_size)
    first_part_cut = start - offset
    last_part_cut = (end % chunk_size) + 1
    part_count = math.ceil(end / chunk_size) - math.floor(offset / chunk_size)

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
        "user_name": token_data.get("name", "Unknown") if token_data else "Unknown"
    }

    parallelism    = Telegram.PARALLEL    # concurrent Telegram GetFile requests
    prefetch_count = Telegram.PRE_FETCH   # pre-fetch buffer queue size (chunks)

    # Gather extra bot clients so each parallel GetFile slot uses a different
    # Telegram account — different rate-limit buckets, no per-session FloodWait.
    extra_clients_for_stream = []
    if parallelism > 1 and len(multi_clients) > 1:
        other_indices = sorted(
            (i for i in multi_clients if i != index),
            key=lambda i: work_loads.get(i, 0),
        )

        async def _get_extra_file_id(ec_idx: int):
            ec_client = multi_clients[ec_idx]
            if ec_client not in _streamer_by_client:
                _streamer_by_client[ec_client] = ByteStreamer(ec_client, ec_idx)
            ec_streamer = _streamer_by_client[ec_client]
            try:
                ec_fid = await ec_streamer.get_file_properties(chat_id=chat_id, message_id=msg_id)
                return (ec_idx, ec_streamer, ec_fid)
            except Exception as e:
                LOGGER.warning("Extra client %s file_id fetch failed: %s", ec_idx, e)
                return None

        results = await asyncio.gather(*[
            _get_extra_file_id(i) for i in other_indices[:parallelism - 1]
        ])
        extra_clients_for_stream = [r for r in results if r is not None]

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
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        }

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
        "Cache-Control": "public, max-age=3600",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    }

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
            "client_dc_map": client_dc_map,
            "work_loads": work_loads,
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
