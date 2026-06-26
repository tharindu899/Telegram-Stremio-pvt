import asyncio
import json
from fastapi import Request, Query, HTTPException
from fastapi.responses import StreamingResponse
from Backend import db, StartTime, __version__
from Backend.logger import LOGGER
from Backend.helper.pyro import get_readable_time
from Backend.helper.media_types import canonical_media_type
from Backend.helper.metadata import (
    search_movie_candidates,
    search_tv_candidates,
    fetch_selected_movie_metadata,
    fetch_selected_tv_metadata,
)
from Backend.pyrofork.bot import multi_clients, StreamBot
from Backend.helper.custom_dl import run_speed_test, _speed_test_single_client
from time import time
from Backend.helper.runtime_config import (
    build_config_payload, apply_runtime_config, CONFIG_SCHEMA, is_webui_locked,
    serialize_value, validate_runtime_config,
)
from Backend.helper.secure_tokens import BotTokenValidationError, parse_bot_tokens, token_id
from Backend.pyrofork.clients import get_multi_bot_runtime_status, sync_extra_clients
from Backend.helper.auto_catalog import (
    start_auto_catalog_sync_background,
    get_auto_catalog_sync_status,
    get_auto_catalog_settings,
    update_auto_catalog_settings,
)


# --- Runtime Config WebUI API ---
async def get_runtime_config_api():
    try:
        values = await db.get_runtime_config_values()
        return build_config_payload(values)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def update_runtime_config_api(payload: dict):
    try:
        values = (payload or {}).get("values", {}) if isinstance(payload, dict) else {}
        if not isinstance(values, dict):
            raise HTTPException(status_code=422, detail="Configuration values must be an object.")

        current = await db.get_runtime_config_values()
        merged = dict(current or {})
        changed = {}
        locked = []
        invalid = []

        for key, value in values.items():
            if key not in CONFIG_SCHEMA:
                invalid.append(key)
                continue
            if is_webui_locked(key):
                locked.append(key)
                continue
            clean_value = serialize_value(key, value)
            merged[key] = clean_value
            changed[key] = clean_value

        errors = validate_runtime_config(merged)
        if errors:
            message = " ".join(f"{key}: {reason}" for key, reason in errors.items())
            raise HTTPException(status_code=422, detail=message)

        if changed:
            saved = await db.save_runtime_config_values(merged, updated_by="webui")
            applied = apply_runtime_config(changed, source="webui")
        else:
            saved = merged
            applied = {}

        message = f"Saved {len(changed)} setting(s) and applied them live." if changed else "No editable settings changed."
        return {
            "ok": True,
            "message": message,
            "changed": changed,
            "applied": applied,
            "locked": locked,
            "invalid": invalid,
            "config": build_config_payload(saved),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Multi-Bot Config WebUI API ---
async def get_multi_bot_config_api():
    try:
        summary = await db.get_multi_bot_tokens_summary()
        runtime = get_multi_bot_runtime_status()
        return {
            "ok": True,
            "saved_count": summary.get("saved_count", 0),
            "saved_bots": summary.get("saved_bots", []),
            "updated_at": summary.get("updated_at"),
            "runtime": runtime,
            "note": (
                "Add extra BotFather tokens below to spread Telegram streaming traffic. "
                "Saved tokens are encrypted in MongoDB and never sent back to the browser. "
                "Legacy MULTI_TOKEN* host secrets remain active until you remove them from your host."
            ),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def add_multi_bot_tokens_api(payload: dict):
    try:
        raw_tokens = (payload or {}).get("tokens", []) if isinstance(payload, dict) else []
        try:
            incoming = parse_bot_tokens(raw_tokens)
        except BotTokenValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if not incoming:
            raise HTTPException(status_code=422, detail="Paste at least one complete BotFather token.")

        existing = await db.get_multi_bot_tokens()
        merged: list[str] = []
        seen: set[str] = set()
        for token in [*existing, *incoming]:
            fingerprint = token_id(token)
            if fingerprint not in seen:
                merged.append(token)
                seen.add(fingerprint)

        summary = await db.save_multi_bot_tokens(merged, updated_by="webui")
        sync = await sync_extra_clients(merged, source="webui")
        return {
            "ok": True,
            "message": f"Saved {len(incoming)} stream bot(s). The active pool now has {sync.get('total_clients', 1)} client(s).",
            "summary": summary,
            "runtime": get_multi_bot_runtime_status(),
            "sync": sync,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def remove_multi_bot_token_api(token_identifier: str):
    try:
        summary = await db.remove_multi_bot_token(token_identifier, updated_by="webui")
        tokens = await db.get_multi_bot_tokens()
        sync = await sync_extra_clients(tokens, source="webui")
        return {
            "ok": True,
            "message": "Saved stream bot removed from the active pool.",
            "summary": summary,
            "runtime": get_multi_bot_runtime_status(),
            "sync": sync,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# --- API Routes for System Stats ---

async def get_system_stats_api():
    try:
        db_stats = await db.get_database_stats()
        total_movies = sum(stat.get("movie_count", 0) for stat in db_stats)
        total_tv_shows = sum(stat.get("tv_count", 0) for stat in db_stats)
        api_tokens = await db.get_all_api_tokens()
        
        return {
            "server_status": "running",
            "uptime": get_readable_time(time() - StartTime),
            "telegram_bot": f"@{getattr(StreamBot, 'username', '')}" if StreamBot and getattr(StreamBot, 'username', None) else "@StreamBot",
            "connected_bots": len(multi_clients),
            "version": __version__,
            "movies": total_movies,
            "tv_shows": total_tv_shows,
            "databases": db_stats,
            "total_databases": len(db_stats),
            "current_db_index": db.current_db_index,
            "api_tokens": api_tokens
        }
    except Exception as e:
        LOGGER.debug(f"System Stats API Error: {e}")
        return {
            "server_status": "error", 
            "error": str(e)
        }
    
# --- API Routes for Media Management ---

def _media_subtitle_summary(media: dict, media_type: str) -> dict:
    """Build a compact subtitle summary for Media Management cards.

    The dashboard should not expose raw subtitle payloads, only a count,
    detected language codes and (for TV) one episode target for the editor.
    """
    subtitles: list[dict] = []
    first_target: tuple[int | None, int | None] = (None, None)
    episodes_with_subtitles = 0

    if media_type == "movie":
        subtitles = [s for s in (media.get("subtitles") or []) if isinstance(s, dict)]
    else:
        for season in media.get("seasons") or []:
            season_no = season.get("season_number")
            for episode in season.get("episodes") or []:
                episode_subs = [s for s in (episode.get("subtitles") or []) if isinstance(s, dict)]
                if not episode_subs:
                    continue
                episodes_with_subtitles += 1
                subtitles.extend(episode_subs)
                if first_target == (None, None):
                    first_target = (season_no, episode.get("episode_number"))

    languages = []
    seen = set()
    for subtitle in subtitles:
        lang = str(subtitle.get("language") or "und").strip().lower()
        if lang and lang not in seen:
            seen.add(lang)
            languages.append(lang)

    return {
        "subtitle_count": len(subtitles),
        "subtitle_languages": languages[:6],
        "subtitle_episode_count": episodes_with_subtitles,
        "subtitle_target_season": first_target[0],
        "subtitle_target_episode": first_target[1],
    }


async def _enrich_media_with_subtitle_summaries(items: list[dict], media_type: str) -> list[dict]:
    """Add subtitle card fields without changing the public media schema."""
    enriched: list[dict] = []
    detail_field = "subtitles" if media_type == "movie" else "seasons"

    for item in items or []:
        card = dict(item)
        detailed = card
        if detail_field not in card:
            try:
                db_index = int(card.get("db_index"))
                tmdb_id = int(card.get("tmdb_id"))
                detailed = await db.get_document(media_type, tmdb_id, db_index) or card
            except Exception:
                detailed = card
        card.update(_media_subtitle_summary(detailed, media_type))
        enriched.append(card)
    return enriched


async def list_media_api(
    media_type: str = Query("movie", pattern="^(movie|tv)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    search: str = Query("", max_length=100)
):
    media_type = canonical_media_type(media_type)
    try:
        media_key = "movies" if media_type == "movie" else "tv_shows"
        if search:
            result = await db.search_documents(search, page, page_size)
            filtered_results = [
                item for item in result['results']
                if canonical_media_type(item.get('media_type', 'movie')) == media_type
            ]
            total_filtered = len(filtered_results)
            start_index = (page - 1) * page_size
            end_index = start_index + page_size
            paged_results = await _enrich_media_with_subtitle_summaries(
                filtered_results[start_index:end_index], media_type
            )
            return {
                "total_count": total_filtered,
                "current_page": page,
                "total_pages": (total_filtered + page_size - 1) // page_size,
                media_key: paged_results,
            }

        payload = (
            await db.sort_movies([], page, page_size)
            if media_type == "movie"
            else await db.sort_tv_shows([], page, page_size)
        )
        payload[media_key] = await _enrich_media_with_subtitle_summaries(
            payload.get(media_key, []), media_type
        )
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def delete_media_api(
    tmdb_id: int,
    db_index: int,
    media_type: str = Query(pattern="^(movie|tv)$")
):
    media_type = canonical_media_type(media_type)
    try:
        media_type_formatted = "Movie" if media_type == "movie" else "Series"
        result = await db.delete_document(media_type_formatted, tmdb_id, db_index)
        if result:
            return {"message": "Media deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Media not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def update_media_api(
    request: Request,
    tmdb_id: int,
    db_index: int,
    media_type: str = Query(pattern="^(movie|tv)$")
):
    media_type = canonical_media_type(media_type)
    try:
        update_data = await request.json()
        if 'rating' in update_data and update_data['rating']:
            try:
                update_data['rating'] = float(update_data['rating'])
            except (ValueError, TypeError):
                update_data['rating'] = 0.0
        
        if 'release_year' in update_data and update_data['release_year']:
            try:
                update_data['release_year'] = int(update_data['release_year'])
            except (ValueError, TypeError):
                pass
        if 'genres' in update_data:
            if isinstance(update_data['genres'], str):
                update_data['genres'] = [g.strip() for g in update_data['genres'].split(',') if g.strip()]
            elif not isinstance(update_data['genres'], list):
                update_data['genres'] = []
        
        if 'languages' in update_data:
            if isinstance(update_data['languages'], str):
                update_data['languages'] = [l.strip() for l in update_data['languages'].split(',') if l.strip()]
            elif not isinstance(update_data['languages'], list):
                update_data['languages'] = []
        if media_type == "movie":
            if 'runtime' in update_data and update_data['runtime']:
                try:
                    update_data['runtime'] = int(update_data['runtime'])
                except (ValueError, TypeError):
                    pass
        elif media_type == "tv":
            if 'total_seasons' in update_data and update_data['total_seasons']:
                try:
                    update_data['total_seasons'] = int(update_data['total_seasons'])
                except (ValueError, TypeError):
                    pass
            
            if 'total_episodes' in update_data and update_data['total_episodes']:
                try:
                    update_data['total_episodes'] = int(update_data['total_episodes'])
                except (ValueError, TypeError):
                    pass
        update_data = {k: v for k, v in update_data.items() if v != ""}
        result = await db.update_document(media_type, tmdb_id, db_index, update_data)
        if result:
            return {"message": "Media updated successfully"}
        else:
            raise HTTPException(status_code=404, detail="Media not found or no changes made")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def get_media_details_api(
    tmdb_id: int,
    db_index: int,
    media_type: str = Query(pattern="^(movie|tv)$")
):
    media_type = canonical_media_type(media_type)
    try:
        result = await db.get_document(media_type, tmdb_id, db_index)
        if result:
            return result
        else:
            raise HTTPException(status_code=404, detail="Media not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def delete_movie_quality_api(tmdb_id: int, db_index: int, id: str):
    try:
        result = await db.delete_movie_quality(tmdb_id, db_index, id)
        if result:
            return {"message": "Quality deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Quality not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def delete_tv_quality_api(
    tmdb_id: int, db_index: int, season: int, episode: int, id: str
):
    try:
        result = await db.delete_tv_quality(tmdb_id, db_index, season, episode, id)
        if result:
            return {"message": "deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Quality not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def delete_tv_episode_api(
    tmdb_id: int, db_index: int, season: int, episode: int
):
    try:
        result = await db.delete_tv_episode(tmdb_id, db_index, season, episode)
        if result:
            return {"message": "Episode deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Episode not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def delete_tv_season_api(tmdb_id: int, db_index: int, season: int):
    try:
        result = await db.delete_tv_season(tmdb_id, db_index, season)
        if result:
            return {"message": "Season deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Season not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- API Routes for Token Management ---

async def create_token_api(payload: dict):
    try:
        token_name = payload.get("name")
        daily_limit = payload.get("daily_limit_gb")
        monthly_limit = payload.get("monthly_limit_gb")
        user_id = payload.get("user_id")
        
        if not token_name:
             raise HTTPException(status_code=400, detail="Token name is required")

        if user_id not in (None, ""):
            try:
                user_id = int(user_id)
                if user_id < 1:
                    raise ValueError
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="user_id must be a valid Telegram numeric ID")
        else:
            user_id = None
        def parse_limit(val):
            try:
                v = float(val)
                return v if v > 0 else None
            except (ValueError, TypeError):
                return None

        new_token = await db.add_api_token(
            token_name, 
            parse_limit(daily_limit), 
            parse_limit(monthly_limit),
            user_id=user_id
        )
        return new_token
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def update_token_limits_api(token: str, payload: dict):
    try:
        daily_limit = payload.get("daily_limit_gb")
        monthly_limit = payload.get("monthly_limit_gb")
        
        def parse_limit(val):
            try:
                v = float(val)
                return v if v > 0 else None
            except (ValueError, TypeError, AttributeError):
                return None

        result = await db.update_api_token_limits(
            token,
            parse_limit(daily_limit),
            parse_limit(monthly_limit)
        )
        
        if result:
            return {"message": "Limits updated successfully"}
        else:
            return {"message": "Limits updated successfully"}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def revoke_token_api(token: str):
    try:
        result = await db.revoke_api_token(token)
        if result:
            return {"message": "Token revoked successfully"}
        else:
            raise HTTPException(status_code=404, detail="Token not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Speed Test API ---

async def speed_test_api(
    quality_id: str = Query(..., description="Encoded quality ID from DB"),
    tmdb_id: int = Query(...),
    db_index: int = Query(...),
    media_type: str = Query(..., pattern="^(movie|tv)$"),
):
    """
    Decode quality_id using the same decode_string logic as the stream handler,
    then run a parallel download speed test across all connected bot clients.
    """
    from Backend.helper.encrypt import decode_string

    try:
        decoded = await decode_string(quality_id)
        msg_id  = decoded.get("msg_id")
        raw_cid = decoded.get("chat_id")

        if not msg_id or not raw_cid:
            raise HTTPException(
                status_code=422,
                detail=f"Decoded quality data is missing msg_id or chat_id. Decoded: {decoded}"
            )

        # Stream handler adds -100 prefix for channel IDs
        chat_id = int(f"-100{raw_cid}")

        results = await run_speed_test(int(chat_id), int(msg_id))
        return {"results": results, "total_clients_tested": len(results)}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Speed Test SSE Streaming API ---

async def speed_test_stream_api(
    quality_id: str,
    tmdb_id: int,
    db_index: int,
    media_type: str,
):
    """
    SSE version of the speed test. Streams each per-client result as a
    'data:' event the moment that client finishes, so the UI can update live.
    """
    from Backend.helper.encrypt import decode_string

    async def event_generator():
        # Decode quality_id → chat_id + message_id
        try:
            decoded = await decode_string(quality_id)
            msg_id  = decoded.get("msg_id")
            raw_cid = decoded.get("chat_id")
            if not msg_id or not raw_cid:
                payload = json.dumps({"type": "error", "message": f"Cannot decode quality_id. Got: {decoded}"})
                yield f"data: {payload}\n\n"
                return
            chat_id = int(f"-100{raw_cid}")
        except Exception as exc:
            payload = json.dumps({"type": "error", "message": str(exc)})
            yield f"data: {payload}\n\n"
            return

        total = len(multi_clients)
        if total == 0:
            payload = json.dumps({"type": "error", "message": "No bot clients connected"})
            yield f"data: {payload}\n\n"
            return
            
        # Try to resolve the FileId to get the target DC
        target_dc = "?"
        try:
            from Backend.helper.custom_dl import ByteStreamer
            primary_client = multi_clients.get(0) or next(iter(multi_clients.values()))
            streamer = ByteStreamer(primary_client)
            file_id = await streamer.get_file_properties(chat_id, int(msg_id))
            target_dc = file_id.dc_id
        except Exception:
            pass

        # Send initial "start" event so the frontend can set up the table
        yield f"data: {json.dumps({'type': 'start', 'total': total, 'target_dc': target_dc})}\n\n"

        # Run all clients in parallel; feed results into a queue as they finish
        queue: asyncio.Queue = asyncio.Queue()

        async def run_one(client, idx):
            async def on_progress(prog_data):
                await queue.put({"type": "progress", "data": prog_data})
                
            result = await _speed_test_single_client(
                client, idx, chat_id, int(msg_id), progress_callback=on_progress
            )
            await queue.put({"type": "result", "data": result})

        tasks = [
            asyncio.create_task(run_one(client, idx))
            for idx, client in multi_clients.items()
        ]

        completed = 0
        while completed < total:
            msg = await queue.get()
            
            if msg["type"] == "progress":
                payload = json.dumps(msg)
                yield f"data: {payload}\n\n"
            
            elif msg["type"] == "result":
                completed += 1
                payload = json.dumps({
                    "type": "result",
                    "data": msg["data"],
                    "completed": completed,
                    "total": total,
                })
                yield f"data: {payload}\n\n"

        # Wait for any remaining tasks (should already be done)
        await asyncio.gather(*tasks, return_exceptions=True)

        # Final done event
        yield f"data: {json.dumps({'type': 'done', 'total': total})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # prevent nginx from buffering SSE
        },
    )

# ---------------------------------------------------------------------------
# Admin API Routes
# ---------------------------------------------------------------------------

async def get_admin_stats_api() -> dict:
    """Admin dashboard bot stats.

    Older builds only showed ``work_loads`` and ``client_avg_mbps``. That is
    easy to miss on Stremio/VLC because one playback is made from many short
    HTTP range requests; when a range finishes the active counter immediately
    drops back to 0. Split ZIP / split-video virtual streams also update the
    shared ACTIVE_STREAMS registry, so include recent finished ranges as a
    short grace window and use recent stream speeds for the visible average.
    """
    from Backend.pyrofork.bot import work_loads, multi_clients, client_failures, client_avg_mbps
    from Backend.fastapi.routes.stream_routes import _streamer_by_client
    from Backend.helper.custom_dl import ACTIVE_STREAMS, RECENT_STREAMS

    # Sum cache entries across all active ByteStreamer instances
    cache_size = sum(len(getattr(s, "_file_id_cache", {}) or {}) for s in _streamer_by_client.values())

    now = time()
    active_by_client = {idx: 0 for idx in multi_clients}
    recent_by_client = {idx: 0 for idx in multi_clients}
    speed_by_client = {idx: [] for idx in multi_clients}

    def _client_index(info: dict):
        try:
            return int(info.get("client_index"))
        except Exception:
            return None

    def _age(info: dict) -> float:
        ts = info.get("last_ts") or info.get("end_ts") or info.get("start_ts") or 0
        try:
            return now - float(ts)
        except Exception:
            return 999999.0

    def _speed(info: dict) -> float:
        for key in ("instant_mbps", "avg_mbps", "peak_mbps"):
            try:
                value = float(info.get(key) or 0.0)
            except Exception:
                value = 0.0
            if value > 0:
                return value
        return 0.0

    # Real currently-open HTTP streams.
    for info in list(ACTIVE_STREAMS.values()):
        if not isinstance(info, dict):
            continue
        idx = _client_index(info)
        if idx not in multi_clients:
            continue
        if str(info.get("status", "active")).lower() == "active":
            active_by_client[idx] += 1
        spd = _speed(info)
        if spd > 0:
            speed_by_client[idx].append(spd)

    # Recent range requests. VLC/Stremio may finish a 50/128MB range before the
    # dashboard refreshes, so keep it visible briefly and use it for avg speed.
    for info in list(RECENT_STREAMS)[:250]:
        if not isinstance(info, dict):
            continue
        idx = _client_index(info)
        if idx not in multi_clients:
            continue
        age = _age(info)
        if age <= 45:
            recent_by_client[idx] += 1
        if age <= 900:  # last 15 minutes for displayed average speed
            spd = _speed(info)
            if spd > 0:
                speed_by_client[idx].append(spd)

    bot_stats = []
    for client_index in multi_clients:
        raw_load = work_loads.get(client_index, 0)
        try:
            raw_load = int(raw_load)
        except Exception:
            raw_load = 0
        raw_load = max(0, raw_load)

        # Show live requests first, but keep recently-finished range requests
        # visible so playback does not look like 0 while the video is still open.
        load = max(raw_load, active_by_client.get(client_index, 0), recent_by_client.get(client_index, 0))

        failures = client_failures.get(client_index, 0)
        try:
            failures = int(failures)
        except Exception:
            failures = 0

        speeds = speed_by_client.get(client_index) or []
        if speeds:
            # Latest/recent range speeds are more useful than a stale global EMA.
            mbps = sum(speeds[:20]) / max(len(speeds[:20]), 1)
        else:
            try:
                mbps = float(client_avg_mbps.get(client_index, 0.0) or 0.0)
            except Exception:
                mbps = 0.0

        status = "healthy"
        if failures > 5:
            status = "degraded"
        if failures > 15:
            status = "failing"

        bot_stats.append({
            "client_index": client_index,
            "display_name": f"Bot {client_index + 1}",
            "current_load": load,
            "active_streams": active_by_client.get(client_index, 0),
            "recent_streams": recent_by_client.get(client_index, 0),
            "failures": failures,
            "avg_mbps": round(mbps, 2),
            "status": status,
        })

    return {
        "cache_size": cache_size,
        "total_bots": len(multi_clients),
        "bot_workloads": bot_stats,
    }

async def clear_cache_api() -> dict:
    from Backend.fastapi.routes.stream_routes import _streamer_by_client
    from Backend.logger import LOGGER
    
    # Clear cache across all active ByteStreamer instances
    total_cleared = sum(len(s._file_id_cache) for s in _streamer_by_client.values())
    for streamer in _streamer_by_client.values():
        streamer._file_id_cache.clear()
    LOGGER.info(f"Admin cleared the FileId cache ({total_cleared} items purged across {len(_streamer_by_client)} clients).")
    
    return {"status": "success", "message": f"{total_cleared} cached items cleared."}

async def get_dead_links_api() -> dict:
    from Backend import db
    try:
        dead_links = await db.get_all_dead_links()
        return {"status": "success", "data": dead_links}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def get_stream_analytics_api() -> dict:
    from Backend import db
    try:
        data = await db.get_stream_analytics(limit=200)
        return {"status": "success", "data": data}
    except Exception as e:
        from Backend.logger import LOGGER
        LOGGER.error(f"Stream analytics API error: {e}")
        return {"status": "error", "message": str(e)}

async def clear_stream_analytics_api() -> dict:
    try:
        result = await db.dbs["tracking"]["stream_analytics"].delete_many({})
        LOGGER.info(f"Admin cleared stream analytics ({result.deleted_count} records deleted).")

        return {
            "status": "success",
            "message": f"{result.deleted_count} analytics records cleared."
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ---------------------------------------------------------------------------
# Admin Subscription Management API Routes
# ---------------------------------------------------------------------------

async def get_subscription_plans_api() -> dict:
    from Backend import db
    try:
        plans = await db.get_subscription_plans()
        return {"status": "success", "data": plans}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def add_subscription_plan_api(payload: dict) -> dict:
    from Backend import db
    try:
        days = int(payload.get("days", 0))
        price = float(payload.get("price", 0.0))
        if days <= 0 or price < 0:
            raise HTTPException(status_code=400, detail="Invalid plan parameters")
            
        plan_id = await db.add_subscription_plan(days, price)
        if plan_id:
            return {"status": "success", "message": "Plan added successfully", "plan_id": plan_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to add plan")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def update_subscription_plan_api(plan_id: str, payload: dict) -> dict:
    from Backend import db
    try:
        days = int(payload.get("days", 0))
        price = float(payload.get("price", 0.0))
        if days <= 0 or price < 0:
             raise HTTPException(status_code=400, detail="Invalid plan parameters")
             
        success = await db.update_subscription_plan(plan_id, days, price)
        if success:
             return {"status": "success", "message": "Plan updated successfully"}
        else:
             raise HTTPException(status_code=404, detail="Plan not found or update failed")
    except HTTPException:
         raise
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))

async def delete_subscription_plan_api(plan_id: str) -> dict:
    from Backend import db
    try:
        success = await db.delete_subscription_plan(plan_id)
        if success:
            return {"status": "success", "message": "Plan deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Plan not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def get_all_subscribers_api() -> dict:
    from Backend import db
    try:
        users = await db.get_all_subscribers()
        return {"status": "success", "data": users}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def manage_subscriber_api(user_id: int, payload: dict) -> dict:
    from Backend import db
    try:
        action = payload.get("action")
        days = int(payload.get("days", 0))
        
        if action not in ["extend", "reduce", "delete"]:
            raise HTTPException(status_code=400, detail="Invalid action")
            
        success = await db.manage_subscriber(user_id, action, days)
        if success:
            return {"status": "success", "message": "User subscription updated successfully"}
        else:
            raise HTTPException(status_code=404, detail="User not found or update failed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Access Management API ---

async def get_all_tokens_api() -> dict:
    from Backend import db
    from Backend.config import Telegram
    from datetime import datetime
    try:
        tokens = await db.get_all_api_tokens()
        now = datetime.utcnow()
        result = []

        # Pre-load all subscribers into a dict keyed by user_id for O(1) lookup
        subscriber_map = {}       # user_id (str) -> user doc
        if Telegram.SUBSCRIPTION:
            try:
                for u in await db.get_all_subscribers():
                    uid = str(u.get("_id"))
                    subscriber_map[uid] = u
            except Exception:
                pass

        def display_name(user, user_id, token_name=None):
            """Return a non-empty display name for a user."""
            if user:
                n = user.get("first_name") or user.get("username")
                if n:
                    return n
            # Fall back to the name stored on the token itself (set at creation time)
            if token_name:
                return token_name
            return f"User {user_id}" if user_id else "Telegram User"

        def build_entry(user_id, user, token_doc):
            """Build a unified access entry from optional user + token records."""
            expiry = None
            sub_status = None
            user_found = bool(user)

            if user:
                sub_status = user.get("subscription_status")
                expiry = user.get("subscription_expiry")

            # Token-level expiry as fallback
            if token_doc:
                t_expiry = token_doc.get("subscription_expiry") or token_doc.get("expires_at")
                if t_expiry and not expiry:
                    expiry = t_expiry

            # Determine status
            if Telegram.SUBSCRIPTION:
                if not user_found:
                    is_expired = True
                elif sub_status != "active":
                    is_expired = True
                elif not expiry:
                    is_expired = True
                else:
                    is_expired = expiry < now
            else:
                is_expired = bool(expiry and expiry < now)

            token_str = token_doc.get("token") if token_doc else None
            created = token_doc.get("created_at") if token_doc else (user.get("created_at") if user else None)

            return {
                "token": token_str,
                "user_id": user_id,
                "user_name": display_name(user, user_id, token_doc.get("name") if token_doc else None),
                "user_found": user_found,
                "has_token": bool(token_str),
                "created_at": created.isoformat() if created else None,
                "expires_at": expiry.isoformat() if expiry else None,
                "is_expired": is_expired,
                "sub_status": sub_status,
                "addon_url": (
                    f"{Telegram.BASE_URL}/stremio/{token_str}/manifest.json"
                    if token_str else None
                ),
            }

        # Track user_ids that are already represented via a token row
        seen_user_ids = set()

        # --- 1. Process all existing tokens ---
        for t in tokens:
            token_user_id = t.get("user_id")

            # Try to resolve user from subscriber_map using token's user_id
            user = None
            if token_user_id:
                uid_str = str(token_user_id)
                user = subscriber_map.get(uid_str)
                if not user:
                    # Fallback: query DB if not in subscriber_map (e.g. non-active subscribers)
                    try:
                        user = await db.get_user(int(token_user_id))
                    except Exception:
                        pass
                seen_user_ids.add(uid_str)

            result.append(build_entry(token_user_id, user, t))

        # --- 2. Add subscribers who have NO token ---
        for uid_str, u in subscriber_map.items():
            if uid_str in seen_user_ids:
                continue  # already covered by a token row
            result.append(build_entry(u.get("_id"), u, None))

        # Sort: active-with-token first, then active-no-token, expired last
        result.sort(key=lambda x: (x["is_expired"], not x["has_token"]))
        return {"tokens": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def revoke_token_api(token: str) -> dict:
    from Backend import db
    try:
        success = await db.revoke_api_token(token)
        if success:
            return {"status": "success", "message": "Token revoked."}
        raise HTTPException(status_code=404, detail="Token not found.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def assign_plan_api(user_id: int, days: int) -> dict:
    """Assign (or extend) a subscription for any user by user_id, even if not in DB."""
    from Backend import db
    try:
        if days < 1:
            raise HTTPException(status_code=400, detail="Days must be at least 1.")
        result = await db.assign_subscription(user_id, days)
        return {"status": "success", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def link_token_user_api(token: str, user_id: int) -> dict:
    """Link an orphan token (no user_id) to a Telegram user_id."""
    from Backend import db
    try:
        success = await db.link_token_user(token, user_id)
        if success:
            return {"status": "success", "message": f"Token linked to user {user_id}."}
        raise HTTPException(status_code=404, detail="Token not found or already linked.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




async def search_media_rescan_api(media_type: str, query: str, year: int | None = None):
    media_type = canonical_media_type(media_type)
    query = (query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required.")

    if media_type == "movie":
        results = await search_movie_candidates(query=query, year=year)
    else:
        results = await search_tv_candidates(query=query)

    return {"results": results}


async def apply_media_rescan_api(request: Request, tmdb_id: int, db_index: int, media_type: str):
    requested_media_type = media_type
    media_type = canonical_media_type(media_type)
    body = await request.json()
    selected_id = str(body.get("selected_id") or "").strip()

    if not selected_id:
        raise HTTPException(status_code=400, detail="selected_id is required.")

    current_doc = await db.get_document(media_type, tmdb_id, db_index)
    if not current_doc:
        actual_type = await db.find_document_type(tmdb_id, db_index)
        if actual_type:
            LOGGER.warning(
                "Metadata rescan type mismatch: tmdb_id=%s storage_%s requested=%s normalized=%s actual=%s",
                tmdb_id, db_index, requested_media_type, media_type, actual_type,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This title is stored as {actual_type}. Reload the media page and open it "
                    "from the correct category before rescanning metadata."
                ),
            )
        LOGGER.warning(
            "Metadata rescan target missing: tmdb_id=%s storage_%s requested=%s normalized=%s",
            tmdb_id, db_index, requested_media_type, media_type,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                f"Media ID {tmdb_id} was not found in storage_{db_index}. Reload Media Management "
                "and reopen the title before applying the metadata match."
            ),
        )

    metadata = (
        await fetch_selected_movie_metadata(selected_id)
        if media_type == "movie"
        else await fetch_selected_tv_metadata(selected_id)
    )

    if not metadata:
        LOGGER.warning(
            "Metadata rescan candidate unavailable: tmdb_id=%s storage_%s type=%s selected_id=%s",
            tmdb_id, db_index, media_type, selected_id,
        )
        raise HTTPException(
            status_code=404,
            detail="The selected metadata record could not be loaded. Pick another result and try again.",
        )

    updated_doc = await db.replace_media_metadata(
        media_type=media_type,
        tmdb_id=tmdb_id,
        db_index=db_index,
        metadata=metadata,
    )

    if not updated_doc:
        LOGGER.error(
            "Metadata rescan replacement failed: tmdb_id=%s storage_%s type=%s",
            tmdb_id, db_index, media_type,
        )
        raise HTTPException(status_code=500, detail="Failed to replace media metadata.")

    return {
        "success": True,
        "message": "Metadata rescanned successfully.",
        "redirect_tmdb_id": updated_doc.get("tmdb_id"),
        "db_index": updated_doc.get("db_index", db_index),
        "media_type": media_type,
        "data": updated_doc,
    }


# --- Custom Catalog APIs ---

def _normalize_media_type(media_type: str) -> str:
    return canonical_media_type(media_type)


async def list_custom_catalogs_api(
    tmdb_id: int | None = None,
    db_index: int | None = None,
    media_type: str | None = None,
):
    try:
        catalogs = await db.get_custom_catalogs()
        if tmdb_id is not None and db_index is not None and media_type:
            normalized_type = _normalize_media_type(media_type)
            for catalog in catalogs:
                catalog["contains_current"] = any(
                    int(item.get("tmdb_id", -1)) == int(tmdb_id)
                    and int(item.get("db_index", -1)) == int(db_index)
                    and item.get("media_type") == normalized_type
                    for item in catalog.get("items", []) or []
                )
        return {"catalogs": catalogs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def create_custom_catalog_api(payload: dict):
    name = (payload.get("name") or "").strip()
    visible = bool(payload.get("visible", True))
    if not name:
        raise HTTPException(status_code=400, detail="Catalog name is required.")

    catalog_id = await db.create_custom_catalog(name=name, visible=visible)
    if not catalog_id:
        raise HTTPException(status_code=500, detail="Failed to create catalog.")

    catalog = await db.get_custom_catalog(catalog_id)
    return {"message": "Catalog created successfully.", "catalog": catalog}


async def update_custom_catalog_api(catalog_id: str, payload: dict):
    name = payload.get("name")
    visible = payload.get("visible") if "visible" in payload else None
    result = await db.update_custom_catalog(catalog_id, name=name, visible=visible)
    if not result:
        catalog = await db.get_custom_catalog(catalog_id)
        if not catalog:
            raise HTTPException(status_code=404, detail="Catalog not found.")
    return {"message": "Catalog updated successfully.", "catalog": await db.get_custom_catalog(catalog_id)}


async def delete_custom_catalog_api(catalog_id: str):
    result = await db.delete_custom_catalog(catalog_id)
    if not result:
        raise HTTPException(status_code=404, detail="Catalog not found.")
    return {"message": "Catalog deleted successfully."}


async def get_custom_catalog_items_api(
    catalog_id: str,
    media_type: str | None = None,
    page: int = 1,
    page_size: int = 24,
):
    try:
        data = await db.get_custom_catalog_items(catalog_id, media_type, page, page_size)
        if not data.get("catalog"):
            raise HTTPException(status_code=404, detail="Catalog not found.")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def search_catalog_media_api(
    query: str,
    media_type: str = "movie",
    page: int = 1,
    page_size: int = 12,
):
    query = (query or "").strip()
    if not query:
        return {"results": [], "total_count": 0}

    try:
        result = await db.search_documents(query, page, page_size)
        normalized_type = _normalize_media_type(media_type)
        filtered = [item for item in result.get("results", []) if item.get("media_type") == normalized_type]
        return {"results": filtered, "total_count": len(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def add_custom_catalog_item_api(catalog_id: str, payload: dict):
    tmdb_id = payload.get("tmdb_id")
    db_index = payload.get("db_index")
    media_type = _normalize_media_type(payload.get("media_type", "movie"))

    if not tmdb_id or not db_index:
        raise HTTPException(status_code=400, detail="tmdb_id and db_index are required.")

    media = await db.get_document(media_type, int(tmdb_id), int(db_index))
    if not media:
        raise HTTPException(status_code=404, detail="Media not found.")

    catalog = await db.get_custom_catalog(catalog_id)
    if not catalog:
        raise HTTPException(status_code=404, detail="Catalog not found.")

    added = await db.add_item_to_custom_catalog(catalog_id, int(tmdb_id), int(db_index), media_type)
    message = "Added to catalog." if added else "Already exists in this catalog."
    return {"message": message, "added": added}


async def remove_custom_catalog_item_api(
    catalog_id: str,
    tmdb_id: int,
    db_index: int,
    media_type: str,
):
    catalog = await db.get_custom_catalog(catalog_id)
    if not catalog:
        raise HTTPException(status_code=404, detail="Catalog not found.")

    removed = await db.remove_item_from_custom_catalog(
        catalog_id, int(tmdb_id), int(db_index), _normalize_media_type(media_type)
    )
    if not removed:
        return {"message": "Item was not in this catalog.", "removed": False}
    return {"message": "Removed from catalog.", "removed": True}


async def auto_sync_custom_catalogs_api(full_rebuild: bool = False):
    try:
        result = await start_auto_catalog_sync_background(db, force=True, full_rebuild=full_rebuild)
        return {"message": result.get("message", "Auto sync started."), "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def auto_catalog_sync_status_api():
    try:
        return {"status": await get_auto_catalog_sync_status(db)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_auto_catalog_settings_api():
    try:
        return {"settings": await get_auto_catalog_settings(db)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def update_auto_catalog_settings_api(payload: dict):
    try:
        enabled_keys = payload.get("enabled_keys", [])
        if not isinstance(enabled_keys, list):
            raise HTTPException(status_code=400, detail="enabled_keys must be a list.")
        settings = await update_auto_catalog_settings(db, enabled_keys)
        return {"message": "Auto catalog settings saved.", "settings": settings}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
# Subtitle Management API
# ─────────────────────────────────────────────────────────────

async def get_all_subtitles_overview_api(
    page: int = 1,
    page_size: int = 50,
    search: str = "",
):
    """List one bounded subtitle page, optionally searched across the full library."""
    try:
        return await db.get_all_subtitles_overview(
            page=page,
            page_size=page_size,
            search=search,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_subtitles_api(
    imdb_id: str,
    season: int | None = None,
    episode: int | None = None,
):
    """List all subtitles for a movie or TV episode."""
    try:
        subs = await db.get_subtitles(
            imdb_id=imdb_id,
            season_number=season,
            episode_number=episode,
        )
        return {"subtitles": subs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _subtitle_id_matches(subtitle: dict, subtitle_id: str) -> bool:
    """Compare subtitle IDs defensively for legacy MongoDB records."""
    return str((subtitle or {}).get("id") or "") == str(subtitle_id or "")


def _bump_document_revision(document: dict) -> None:
    """Keep media cache revision in sync after an admin subtitle mutation."""
    try:
        document["rev"] = int(document.get("rev") or 0) + 1
    except (TypeError, ValueError):
        document["rev"] = 1


async def _remove_subtitle_from_storage(
    storage,
    imdb_id: str,
    subtitle_id: str,
    season: int | None = None,
    episode: int | None = None,
) -> bool:
    """Remove a subtitle from a movie or any matching TV episode in one storage DB.

    The fallback TV search deliberately does not require season/episode. Old rows in
    the All Subtitles page did not send that context, so those records used to be
    treated as movies and returned a false "Subtitle not found" error.
    """
    movie = await storage["movie"].find_one({"imdb_id": imdb_id})
    if movie:
        subtitles = movie.get("subtitles") or []
        kept = [sub for sub in subtitles if not _subtitle_id_matches(sub, subtitle_id)]
        if len(kept) != len(subtitles):
            movie["subtitles"] = kept
            _bump_document_revision(movie)
            await storage["movie"].replace_one({"imdb_id": imdb_id}, movie)
            return True

    tv = await storage["tv"].find_one({"imdb_id": imdb_id})
    if not tv:
        return False

    # Try the requested episode first. If the saved row is old/missing its
    # episode context, fall back to locating the subtitle ID anywhere in the show.
    passes = [True, False] if season is not None and episode is not None else [False]
    for strict_target in passes:
        changed = False
        for season_doc in tv.get("seasons") or []:
            if strict_target and season_doc.get("season_number") != season:
                continue
            for episode_doc in season_doc.get("episodes") or []:
                if strict_target and episode_doc.get("episode_number") != episode:
                    continue
                subtitles = episode_doc.get("subtitles") or []
                kept = [sub for sub in subtitles if not _subtitle_id_matches(sub, subtitle_id)]
                if len(kept) != len(subtitles):
                    episode_doc["subtitles"] = kept
                    changed = True
        if changed:
            _bump_document_revision(tv)
            await storage["tv"].replace_one({"imdb_id": imdb_id}, tv)
            return True

    return False


async def delete_subtitle_api(
    imdb_id: str,
    subtitle_id: str,
    season: int | None = None,
    episode: int | None = None,
):
    """Remove one subtitle entry without deleting the Telegram message itself."""
    try:
        for db_idx in range(db.current_db_index, 0, -1):
            storage = db.dbs[f"storage_{db_idx}"]
            if await _remove_subtitle_from_storage(storage, imdb_id, subtitle_id, season, episode):
                return {"message": "Subtitle deleted."}

        raise HTTPException(status_code=404, detail="Subtitle not found.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def search_media_for_subtitles_api(
    q: str,
    media_type: str = "movie",
):
    """Search movies/TV shows to populate the subtitle admin picker."""
    try:
        media_type = canonical_media_type(media_type)
        data = await db.search_documents(query=q, page=1, page_size=12)
        all_items = data.get("results", data) if isinstance(data, dict) else data
        all_items = [
            item for item in all_items
            if canonical_media_type(item.get("media_type", "movie")) == media_type
        ]
        return {"results": all_items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def add_subtitle_api(
    imdb_id: str,
    body: dict,
):
    """
    Manually add a subtitle from the admin panel.
    Accepts either a Telegram message reference (chat_id + msg_id)
    or a public external URL.
    """
    try:
        from Backend.helper.encrypt import encode_string

        language = body.get("language", "en")
        fmt      = body.get("format", "srt")
        name     = body.get("name") or ""
        season   = body.get("season")
        episode  = body.get("episode")

        if "url" in body and body["url"]:
            # URL-based subtitle — encode with source_type flag
            subtitle_id = await encode_string({"url": body["url"], "source_type": "url"})
            source_type = "url"
            if not name:
                name = body["url"].split("/")[-1] or "subtitle." + fmt
        elif "chat_id" in body and "msg_id" in body:
            # Telegram-based subtitle
            subtitle_id = await encode_string({
                "chat_id": str(body["chat_id"]),
                "msg_id":  str(body["msg_id"]),
            })
            source_type = "telegram"
            if not name:
                name = f"tg_{body['chat_id']}_{body['msg_id']}.{fmt}"
        else:
            raise HTTPException(status_code=400, detail="Provide either 'url' or 'chat_id'+'msg_id'.")

        ok = await db.insert_subtitle(
            imdb_id=imdb_id,
            subtitle_id=subtitle_id,
            language=language,
            name=name,
            fmt=fmt,
            season_number=season,
            episode_number=episode,
            source_type=source_type,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Media not found in database.")

        return {"message": "Subtitle added successfully.", "subtitle_id": subtitle_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _edit_subtitle_in_storage(
    storage,
    imdb_id: str,
    subtitle_id: str,
    update_fields: dict,
    season: int | None = None,
    episode: int | None = None,
) -> bool:
    """Update one subtitle in a movie or TV show, including legacy TV rows."""
    movie = await storage["movie"].find_one({"imdb_id": imdb_id})
    if movie:
        for subtitle in movie.get("subtitles") or []:
            if _subtitle_id_matches(subtitle, subtitle_id):
                subtitle.update(update_fields)
                _bump_document_revision(movie)
                await storage["movie"].replace_one({"imdb_id": imdb_id}, movie)
                return True

    tv = await storage["tv"].find_one({"imdb_id": imdb_id})
    if not tv:
        return False

    # First honour a supplied episode. Then search the whole show as a legacy
    # fallback, so subtitles stored before season/episode context was added stay
    # editable from the All Subtitles screen.
    passes = [True, False] if season is not None and episode is not None else [False]
    for strict_target in passes:
        for season_doc in tv.get("seasons") or []:
            if strict_target and season_doc.get("season_number") != season:
                continue
            for episode_doc in season_doc.get("episodes") or []:
                if strict_target and episode_doc.get("episode_number") != episode:
                    continue
                for subtitle in episode_doc.get("subtitles") or []:
                    if _subtitle_id_matches(subtitle, subtitle_id):
                        subtitle.update(update_fields)
                        _bump_document_revision(tv)
                        await storage["tv"].replace_one({"imdb_id": imdb_id}, tv)
                        return True

    return False


async def edit_subtitle_api(
    imdb_id: str,
    subtitle_id: str,
    body: dict,
):
    """Edit a subtitle language and/or display name without touching Telegram."""
    try:
        language = body.get("language")
        name = body.get("name")
        season = body.get("season")
        episode = body.get("episode")

        if not language and name is None:
            raise HTTPException(status_code=400, detail="Provide at least 'language' or 'name' to update.")

        update_fields = {}
        if language:
            update_fields["language"] = language
        if name is not None:
            update_fields["name"] = name

        for db_idx in range(db.current_db_index, 0, -1):
            storage = db.dbs[f"storage_{db_idx}"]
            if await _edit_subtitle_in_storage(storage, imdb_id, subtitle_id, update_fields, season, episode):
                return {"message": "Subtitle updated."}

        raise HTTPException(status_code=404, detail="Subtitle not found.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Admin Tools / Control Center API
# ---------------------------------------------------------------------------

ADMIN_TOOL_JOBS = {
    "scan": {
        "running": False, "status": "idle", "message": "No scan running.",
        "started_at": None, "finished_at": None, "result": None, "error": None,
    },
    "deadcheck": {
        "running": False, "status": "idle", "message": "No deadcheck running.",
        "started_at": None, "finished_at": None, "result": None, "error": None,
    },
    "dedupe": {
        "running": False, "status": "idle", "message": "No duplicate cleanup running.",
        "started_at": None, "finished_at": None, "result": None, "error": None,
    },
}


def _job_update(name: str, **updates):
    job = ADMIN_TOOL_JOBS.setdefault(name, {})
    job.update(updates)
    return job


def _job_snapshot(name: str) -> dict:
    job = dict(ADMIN_TOOL_JOBS.get(name, {}))
    for key in ("started_at", "finished_at"):
        value = job.get(key)
        if value:
            try:
                job[key] = round(float(value), 3)
            except Exception:
                pass
    return job


def _admin_channels_from_payload(payload: dict) -> list[int]:
    from Backend.config import Telegram
    raw_target = (payload or {}).get("target") or (payload or {}).get("channel") or ""
    if raw_target:
        targets = [str(raw_target).strip()]
    else:
        targets = [str(x).strip() for x in getattr(Telegram, "AUTH_CHANNEL", [])]
    channels = []
    for item in targets:
        if not item:
            continue
        try:
            channels.append(int(item))
        except Exception:
            LOGGER.warning("Admin tools scan ignored invalid channel id: %s", item)
    return channels


async def _run_admin_scan_job(channels: list[int], rescan: bool = False):
    from Backend.pyrofork.bot import StreamBot
    from Backend.pyrofork.plugins import scanner

    _job_update("scan", running=True, status="running", message="Scan started.", started_at=time(), finished_at=None, result=None, error=None)
    try:
        if scanner.scan_state.running:
            raise RuntimeError("A scan is already running.")
        if not StreamBot:
            raise RuntimeError("Telegram bot client is not ready yet.")
        if not channels:
            raise RuntimeError("No channel/group IDs configured.")

        scanner.scan_state.reset()
        scanner.scan_state.running = True
        scanner.scan_state.started_at = time()

        purged = 0
        if rescan:
            _job_update("scan", message="Purging old DB entries before rescan…")
            for channel_id in channels:
                channel_int = int(str(channel_id).replace("-100", "", 1))
                purged += await scanner._purge_channel_entries(channel_int)

        for channel_id in channels:
            if scanner.scan_state.cancelled:
                break
            _job_update("scan", message=f"Scanning {channel_id}…")
            await scanner._scan_channel(StreamBot, int(channel_id))

        dedupe_stats = {}
        if not scanner.scan_state.cancelled:
            _job_update("scan", message="Scan complete. Running safe duplicate cleanup…")
            try:
                dedupe_stats = await db.remove_duplicate_entries(delete_old_messages=True)
            except Exception as e:
                LOGGER.warning("Admin tools post-scan dedupe failed: %s", e)

        s = scanner.scan_state
        result = {
            "cancelled": bool(s.cancelled),
            "purged": purged,
            "channel": s.channel_name,
            "processed": s.processed,
            "messages_found": s.total_found,
            "indexed": s.indexed,
            "indexed_videos": s.indexed_videos,
            "indexed_split_videos": s.indexed_split_videos,
            "indexed_split_zips": s.indexed_split_zips,
            "indexed_subtitles": s.indexed_subtitles,
            "skipped_duplicate": s.skipped_dup,
            "skipped_metadata": s.skipped_meta,
            "skipped_non_video": s.skipped_nonvid,
            "errors": s.errors,
            "elapsed": s.elapsed,
            "dedupe": dedupe_stats,
        }
        _job_update("scan", running=False, status="cancelled" if s.cancelled else "complete", message="Scan cancelled." if s.cancelled else "Scan complete.", finished_at=time(), result=result)
    except Exception as e:
        LOGGER.exception("Admin tools scan failed: %s", e)
        _job_update("scan", running=False, status="error", message="Scan failed.", finished_at=time(), error=str(e))
    finally:
        try:
            scanner.scan_state.running = False
        except Exception:
            pass


async def _run_admin_deadcheck_job():
    from Backend.helper.link_checker import DeadLinkChecker
    from Backend.pyrofork.bot import StreamBot

    _job_update("deadcheck", running=True, status="running", message="Dead-link check running…", started_at=time(), finished_at=None, result=None, error=None)
    try:
        if not StreamBot:
            raise RuntimeError("Telegram bot client is not ready yet.")
        checker = DeadLinkChecker(db, StreamBot)
        result = await checker._scan_all_media()
        _job_update("deadcheck", running=False, status="complete", message="Dead-link check complete.", finished_at=time(), result=result)
    except Exception as e:
        LOGGER.exception("Admin tools deadcheck failed: %s", e)
        _job_update("deadcheck", running=False, status="error", message="Dead-link check failed.", finished_at=time(), error=str(e))


async def _run_admin_dedupe_job(confirm: bool = False):
    _job_update("dedupe", running=True, status="running", message="Checking duplicates…", started_at=time(), finished_at=None, result=None, error=None)
    try:
        if confirm:
            result = await db.remove_duplicate_entries(delete_old_messages=True)
            message = "Duplicate cleanup complete."
        else:
            if hasattr(db, "preview_duplicate_entries"):
                result = await db.preview_duplicate_entries()
            else:
                result = {"movies": 0, "episodes": 0, "subtitles": 0, "old_messages_queued": 0, "note": "Preview not available in this build."}
            message = "Duplicate check complete. Nothing was deleted."
        _job_update("dedupe", running=False, status="complete", message=message, finished_at=time(), result=result)
    except Exception as e:
        LOGGER.exception("Admin tools dedupe failed: %s", e)
        _job_update("dedupe", running=False, status="error", message="Duplicate cleanup failed.", finished_at=time(), error=str(e))


async def admin_tools_status_api() -> dict:
    from Backend.pyrofork.plugins import scanner
    s = scanner.scan_state
    scan_progress = {
        "running": bool(s.running),
        "cancelled": bool(s.cancelled),
        "channel_id": s.channel_id,
        "channel_name": s.channel_name,
        "elapsed": s.elapsed,
        "messages_found": s.total_found,
        "processed": s.processed,
        "indexed": s.indexed,
        "indexed_videos": s.indexed_videos,
        "indexed_split_videos": s.indexed_split_videos,
        "indexed_split_zips": s.indexed_split_zips,
        "indexed_subtitles": s.indexed_subtitles,
        "skipped_duplicate": s.skipped_dup,
        "skipped_metadata": s.skipped_meta,
        "skipped_non_video": s.skipped_nonvid,
        "errors": s.errors,
    }
    return {
        "status": "success",
        "jobs": {name: _job_snapshot(name) for name in ADMIN_TOOL_JOBS},
        "scan_progress": scan_progress,
        "admin_stats": await get_admin_stats_api(),
    }


async def admin_tools_scan_start_api(payload: dict) -> dict:
    from Backend.pyrofork.plugins import scanner
    if scanner.scan_state.running or ADMIN_TOOL_JOBS["scan"].get("running"):
        raise HTTPException(status_code=409, detail="A scan is already running.")
    channels = _admin_channels_from_payload(payload or {})
    mode = str((payload or {}).get("mode") or "scan").lower()
    rescan = mode in ("rescan", "full", "full_rescan")
    asyncio.create_task(_run_admin_scan_job(channels, rescan=rescan))
    return {"status": "started", "mode": "rescan" if rescan else "scan", "channels": channels}


async def admin_tools_scan_cancel_api() -> dict:
    from Backend.pyrofork.plugins import scanner
    if not scanner.scan_state.running:
        return {"status": "idle", "message": "No scan is currently running."}
    scanner.scan_state.cancelled = True
    _job_update("scan", message="Scan cancellation requested…")
    return {"status": "cancelling", "message": "Scan will stop after the current message/batch."}


async def admin_tools_deadcheck_api() -> dict:
    if ADMIN_TOOL_JOBS["deadcheck"].get("running"):
        raise HTTPException(status_code=409, detail="Dead-link check is already running.")
    asyncio.create_task(_run_admin_deadcheck_job())
    return {"status": "started", "message": "Dead-link check started."}


async def admin_tools_dedupe_api(payload: dict) -> dict:
    if ADMIN_TOOL_JOBS["dedupe"].get("running"):
        raise HTTPException(status_code=409, detail="Duplicate cleanup/check is already running.")
    confirm = bool((payload or {}).get("confirm"))
    asyncio.create_task(_run_admin_dedupe_job(confirm=confirm))
    return {"status": "started", "mode": "confirm" if confirm else "check"}


async def admin_tools_clear_cache_api() -> dict:
    """Clear FileId cache plus local /tmp block caches used by turbo streaming."""
    import shutil
    from pathlib import Path
    from Backend.config import Telegram
    base = await clear_cache_api()
    cleared_dirs = []
    errors = []
    dirs = [
        getattr(Telegram, "STREAM_CACHE_DIR", "/tmp/tg_stream_cache"),
        getattr(Telegram, "SPLIT_ZIP_CACHE_DIR", "/tmp/tg_split_zip_cache"),
        "/tmp/tg_split_zip_cache",
        "/tmp/tg_stream_cache",
    ]
    for raw in sorted(set(str(d) for d in dirs if d)):
        path = Path(raw)
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                cleared_dirs.append(str(path))
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"{path}: {e}")
    return {"status": "success" if not errors else "partial", "message": "Cache cleared.", "file_id_cache": base, "cleared_dirs": cleared_dirs, "errors": errors}


async def admin_tools_speed_api() -> dict:
    """Lightweight speed/status snapshot for all bot clients."""
    data = await get_admin_stats_api()
    total_active = sum(int(b.get("active_streams", 0) or 0) for b in data.get("bot_workloads", []))
    total_recent = sum(int(b.get("recent_streams", 0) or 0) for b in data.get("bot_workloads", []))
    avg = 0.0
    speeds = [float(b.get("avg_mbps", 0.0) or 0.0) for b in data.get("bot_workloads", []) if float(b.get("avg_mbps", 0.0) or 0.0) > 0]
    if speeds:
        avg = round(sum(speeds) / len(speeds), 2)
    return {"status": "success", "total_active": total_active, "total_recent": total_recent, "average_mbps": avg, "data": data}


# --- Full Features Pack APIs ---

async def get_full_features_dashboard_api() -> dict:
    """Return live admin analytics.

    The old UI showed a static 32-feature checklist, which was confusing.
    This endpoint now focuses on real working data and is fault-tolerant: if
    one analytics query fails, the rest of the admin tools still load.
    """
    async def safe(name: str, coro, fallback):
        try:
            return await coro
        except Exception as e:
            LOGGER.warning("admin analytics %s failed: %s", name, e)
            return {**fallback, "error": str(e)} if isinstance(fallback, dict) else fallback

    storage = await safe("storage", db.get_storage_usage_summary(), {"summary": {}, "databases": []})
    top = await safe("top_watched", db.get_top_watched(15), {"items": []})
    usage = await safe("token_usage", db.get_token_usage_summary(), {"summary": {}, "tokens": []})
    topics = await safe("topics", db.get_source_topic_stats(), {"topics": [], "total": 0})
    analytics = await safe("stream_analytics", db.get_stream_analytics(50), {"summary": {}, "records": []})

    return {
        "status": "success",
        "storage": storage,
        "top_watched": top,
        "token_usage": usage,
        "topics": topics,
        "analytics": analytics,
        "stream_modes": [
            {"mode": "Direct", "speed": "Fastest", "note": "Normal .mkv/.mp4 Telegram file."},
            {"mode": "Split Video", "speed": "Good", "note": "Movie.mkv.001/.002 with direct byte mapping."},
            {"mode": "Split ZIP", "speed": "Slower", "note": "Works, but ZIP random seeks can buffer more."},
        ],
    }


async def get_storage_usage_api() -> dict:
    return await db.get_storage_usage_summary()


async def get_top_watched_api(limit: int = 15) -> dict:
    return await db.get_top_watched(limit)


async def get_token_usage_api() -> dict:
    return await db.get_token_usage_summary()


async def reset_token_usage_api(token: str) -> dict:
    ok = await db.reset_token_usage(token)
    if not ok:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"status": "success", "message": "Token usage/quota counters reset."}


async def get_topic_stats_api() -> dict:
    return await db.get_source_topic_stats()
