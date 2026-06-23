"""
Channel / topic-group Scanner Plugin — indexes existing channel or Telegram forum topic group content into the database.

Commands (owner-only, private chat):
    /scan          — Scan all AUTH_CHANNELs, skip already-indexed messages
    /scan <id>     — Scan a specific channel/group by numeric parent ID
    /rescan        — Wipe DB entries for AUTH_CHANNELs, then full re-index
    /scanstatus    — Show progress of any running scan
    /cancelscan    — Abort a running scan gracefully
"""

import asyncio
import time
from pyrogram import filters, Client, enums
from pyrogram.types import Message
from pyrogram.errors import FloodWait, ChannelPrivate, ChatAdminRequired

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
from Backend.config import Telegram
from Backend.helper.pyro import clean_filename, get_readable_file_size, remove_urls
from Backend.helper.metadata import metadata
from Backend.helper.encrypt import encode_string
from Backend.helper.split_archive import (
    split_zip_info, strip_split_zip_suffix,
    split_video_info, strip_split_video_suffix,
    is_video_filename,
)
from Backend.pyrofork.plugins.reciever import (
    _is_subtitle, _is_subtitle_message, _subtitle_candidate_name, _subtitle_extension_from_text, _json_safe,
    _parse_sub_caption, _detect_subtitle_language,
    _detect_subtitle_metadata, LANG_NAMES
)


# ── Scan state (singleton — only one scan at a time) ────────────────────
class _ScanState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.running = False
        self.cancelled = False
        self.channel_id = None
        self.channel_name = ""
        self.total_found = 0
        self.processed = 0
        self.indexed = 0
        # Compact per-type counts. These are reported once at scan completion,
        # so /scan and /rescan remain readable without hiding media activity.
        self.indexed_videos = 0
        self.indexed_split_zips = 0
        self.indexed_split_videos = 0
        self.indexed_subtitles = 0
        self.skipped_dup = 0
        self.skipped_meta = 0
        self.skipped_nonvid = 0
        self.errors = 0
        self.started_at = 0.0
        self.status_msg: Message | None = None

    @property
    def elapsed(self) -> str:
        s = int(time.time() - self.started_at) if self.started_at else 0
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"


scan_state = _ScanState()

PROGRESS_EVERY = 15  
RATE_LIMIT_DELAY = 0.3 


def _scan_topic_id(message):
    for attr in ("message_thread_id", "reply_to_top_message_id"):
        val = getattr(message, attr, None)
        if val:
            try:
                return int(val)
            except Exception:
                pass
    return None


def _scan_source_link(message):
    try:
        internal = str(message.chat.id).replace("-100", "", 1)
        topic = _scan_topic_id(message)
        if topic and topic != int(message.id):
            return f"https://t.me/c/{internal}/{topic}/{int(message.id)}"
        return f"https://t.me/c/{internal}/{int(message.id)}"
    except Exception:
        return ""


def _scan_name_candidates(message):
    """Return possible filenames from Telegram document/video + caption.

    Some uploaders send split parts with a generic Telegram filename but put the
    real ``Movie.mkv.001`` / ``Movie.zip.001`` name in the caption. Scanner must
    try both, otherwise /scan and /rescan miss split files.
    """
    values = []
    media = getattr(message, "document", None) or getattr(message, "video", None)
    if media is not None:
        values.append(getattr(media, "file_name", None))
    values.append(getattr(message, "caption", None))

    out = []
    seen = set()
    for value in values:
        if not value:
            continue
        for candidate in str(value).splitlines() or [str(value)]:
            candidate = candidate.strip()
            if not candidate:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def _first_split_zip_info(message):
    for candidate in _scan_name_candidates(message):
        info = split_zip_info(candidate)
        if info:
            return info, candidate
    return None, None


def _first_split_video_info(message):
    for candidate in _scan_name_candidates(message):
        info = split_video_info(candidate)
        if info:
            return info, candidate
    return None, None


def _scan_is_video_message(message) -> bool:
    """Match video documents by MIME type or their actual filename extension.

    Telegram commonly reports MKV uploads as application/octet-stream when
    they are sent as files.  Scanner and live uploads must apply the same rule.
    """
    if getattr(message, "video", None):
        return True

    document = getattr(message, "document", None)
    if not document:
        return False

    mime = (getattr(document, "mime_type", "") or "").lower()
    return mime.startswith("video/") or is_video_filename(getattr(document, "file_name", ""))


# ── Helpers ──────────────────────────────────────────────────────────────

async def _stream_id_exists(channel: int, msg_id: int) -> bool:
    """Check if this (channel, msg_id) combo is already in the DB."""
    try:
        stream_hash = await encode_string({"chat_id": channel, "msg_id": msg_id})
    except Exception:
        return False

    # Check across all storage DBs
    for i in range(1, db.current_db_index + 1):
        storage = db.dbs.get(f"storage_{i}")
        if storage is None:
            continue
        if await storage["movie"].find_one({"telegram.id": stream_hash}):
            return True
        if await storage["tv"].find_one({"seasons.episodes.telegram.id": stream_hash}):
            return True
    return False


def _split_parts_complete(parts: list) -> bool:
    """Return True only when split parts are sequential from 1..N and N >= 2."""
    try:
        numbers = sorted(int(p.get("part", 0)) for p in (parts or []))
        return bool(numbers) and len(numbers) >= 2 and numbers[0] == 1 and numbers == list(range(1, numbers[-1] + 1))
    except Exception:
        return False


def _quality_contains_part(quality: dict, channel: int, msg_id: int) -> bool:
    for part in quality.get("parts") or []:
        try:
            if int(part.get("chat_id")) == int(channel) and int(part.get("msg_id")) == int(msg_id):
                return True
        except Exception:
            continue
    return False


async def _split_part_exists(channel: int, msg_id: int) -> bool:
    """Check if a split part is already indexed as an alive, complete split stream.

    Older builds could leave dead/incomplete split entries in DB. If the only
    matching entry is dead or incomplete, return False so /rescan can rebuild it
    and remove the old dead flag instead of skipping forever.
    """
    channel = int(str(channel).replace("-100", "", 1))
    msg_id = int(msg_id)

    for i in range(1, db.current_db_index + 1):
        storage = db.dbs.get(f"storage_{i}")
        if storage is None:
            continue

        movie_query = {"telegram.parts": {"$elemMatch": {"chat_id": channel, "msg_id": msg_id}}}
        movie = await storage["movie"].find_one(movie_query)
        if movie:
            for quality in movie.get("telegram", []) or []:
                if not _quality_contains_part(quality, channel, msg_id):
                    continue
                if quality.get("is_dead"):
                    return False
                return _split_parts_complete(quality.get("parts") or [])

        tv_query = {"seasons.episodes.telegram.parts": {"$elemMatch": {"chat_id": channel, "msg_id": msg_id}}}
        tv = await storage["tv"].find_one(tv_query)
        if tv:
            for season in tv.get("seasons", []) or []:
                for episode in season.get("episodes", []) or []:
                    for quality in episode.get("telegram", []) or []:
                        if not _quality_contains_part(quality, channel, msg_id):
                            continue
                        if quality.get("is_dead"):
                            return False
                        return _split_parts_complete(quality.get("parts") or [])
    return False


async def _update_progress(force: bool = False):
    """Edit the status message with current scan progress."""
    s = scan_state
    if not s.status_msg:
        return
    if not force and s.processed % PROGRESS_EVERY != 0:
        return
    try:
        text = (
            f"<blockquote>📡 <b>Scanning:</b> {s.channel_name}</blockquote>\n\n"
            f"⏱ Elapsed: <code>{s.elapsed}</code>\n"
            f"📨 Processed: <code>{s.processed}</code>\n"
            f"✅ Indexed: <code>{s.indexed}</code>\n"
            f"⏭ Skipped (duplicate): <code>{s.skipped_dup}</code>\n"
            f"⚠️ Skipped (metadata fail): <code>{s.skipped_meta}</code>\n"
            f"📎 Skipped (non-video): <code>{s.skipped_nonvid}</code>\n"
            f"❌ Errors: <code>{s.errors}</code>"
        )
        await s.status_msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception:
        pass  


async def _scan_channel(client: Client, chat_id: int):
    """Iterate all video messages in a channel and index them.
    
    Bots cannot use get_chat_history or search_messages (both user-only).
    The only reliable bot method is get_messages with explicit IDs.
    
    Strategy: walk forward from ID 1 in batches of 200. Keep going even
    through empty batches (deleted messages / gaps). Stop after 5
    consecutive all-empty batches, which tolerates gaps of up to ~1000 IDs.
    Hard cap at 500,000 to prevent infinite loops.
    """
    s = scan_state

    try:
        chat = await client.get_chat(chat_id)
        s.channel_name = getattr(chat, "title", str(chat_id))
    except (ChannelPrivate, ChatAdminRequired) as e:
        LOGGER.error(f"Cannot access chat {chat_id}: {e}")
        raise
    except Exception as e:
        s.channel_name = str(chat_id)
        LOGGER.warning(f"Could not resolve channel name for {chat_id}: {e}")

    s.channel_id = chat_id
    LOGGER.info(f"[Scanner] Starting scan of {s.channel_name} ({chat_id})")

    BATCH_SIZE = 200
    MAX_EMPTY_BATCHES = 25     # tolerate deleted/sparse channel history gaps (~5000 IDs)
    MAX_ID_CAP = 500_000      
    empty_streak = 0
    current = 1
    split_zip_groups = {}
    split_file_groups = {}
    pending_subtitles = []

    while empty_streak < MAX_EMPTY_BATCHES and current < MAX_ID_CAP:
        if s.cancelled:
            LOGGER.info("[Scanner] Scan cancelled by user.")
            break

        batch_ids = list(range(current, min(current + BATCH_SIZE, MAX_ID_CAP)))

        try:
            messages = await client.get_messages(chat_id, batch_ids)
        except FloodWait as e:
            LOGGER.info(f"[Scanner] FloodWait {e.value}s, sleeping…")
            await asyncio.sleep(e.value)
            try:
                messages = await client.get_messages(chat_id, batch_ids)
            except Exception as ex:
                LOGGER.error(f"[Scanner] Retry failed at {current}: {ex}")
                s.errors += 1
                current += BATCH_SIZE
                empty_streak += 1
                continue
        except Exception as e:
            LOGGER.error(f"[Scanner] Batch fetch error at {current}: {e}")
            s.errors += 1
            current += BATCH_SIZE
            empty_streak += 1
            continue

        if not isinstance(messages, list):
            messages = [messages]

        batch_had_content = False

        for message in messages:
            if s.cancelled:
                break

            if message.empty:
                continue

            batch_had_content = True
            s.total_found += 1

            # ── Split ZIP archive part? ───────────────────────────
            if getattr(Telegram, "SPLIT_ZIP_STREAM", True) and message.document:
                split_info, fname_for_split = _first_split_zip_info(message)
                if split_info:
                    channel_int = int(str(chat_id).replace("-100", ""))
                    key = f"{channel_int}:{split_info['base_name'].lower()}"
                    group = split_zip_groups.setdefault(key, {
                        "channel": channel_int,
                        "base_name": split_info["base_name"],
                        "parts": {},
                    })
                    group["parts"][int(split_info["part_number"])] = {
                        "chat_id": channel_int,
                        "msg_id": int(message.id),
                        "part": int(split_info["part_number"]),
                        "name": fname_for_split,
                        "size_bytes": int(getattr(message.document, "file_size", 0) or 0),
                        "date_added": getattr(message, "date", None),
                        "source_topic_id": _scan_topic_id(message),
                        "source_link": _scan_source_link(message),
                    }
                    s.processed += 1
                    await _update_progress()
                    continue

            # ── Direct split video part? (.mkv.001, .mp4.002, ...) ─
            if message.document:
                split_file_info, fname_for_split = _first_split_video_info(message)
                if split_file_info:
                    channel_int = int(str(chat_id).replace("-100", ""))
                    key = f"{channel_int}:{split_file_info['base_name'].lower()}"
                    group = split_file_groups.setdefault(key, {
                        "channel": channel_int,
                        "base_name": split_file_info["base_name"],
                        "parts": {},
                    })
                    group["parts"][int(split_file_info["part_number"])] = {
                        "chat_id": channel_int,
                        "msg_id": int(message.id),
                        "part": int(split_file_info["part_number"]),
                        "name": fname_for_split,
                        "size_bytes": int(getattr(message.document, "file_size", 0) or 0),
                        "date_added": getattr(message, "date", None),
                        "source_topic_id": _scan_topic_id(message),
                        "source_link": _scan_source_link(message),
                    }
                    s.processed += 1
                    await _update_progress()
                    continue

            # ── Subtitle file? ────────────────────────────────────
            if message.document and _is_subtitle_message(message):
                caption    = message.caption or ""
                fname      = _subtitle_candidate_name(message)
                ext        = (_subtitle_extension_from_text(fname) or ".srt").lstrip(".")
                channel_int = int(str(chat_id).replace("-100", ""))
                msg_id     = message.id
                parsed = _parse_sub_caption(caption)

                if parsed:
                    # Explicit [SUB:tt... lang] caption
                    imdb_id, lang, season, episode = parsed
                    detection_method = "manual-caption"
                else:
                    # Auto-detect: caption first, filename fallback
                    meta, detection_method, _source = await _detect_subtitle_metadata(
                        caption, fname, channel_int, msg_id
                    )

                    if not meta or not meta.get("imdb_id"):
                        s.skipped_nonvid += 1
                        s.processed += 1
                        await _update_progress()
                        continue

                    imdb_id = meta["imdb_id"]
                    lang    = _detect_subtitle_language(caption, fname)
                    season  = meta.get("season_number") or meta.get("season")
                    episode = meta.get("episode_number") or meta.get("episode")

                # Queue subtitles until after video/split streams are indexed.
                # This fixes /rescan when a subtitle message appears before the
                # matching movie/episode message in the Telegram history.
                pending_subtitles.append({
                    "imdb_id": imdb_id,
                    "language": lang,
                    "season_number": season,
                    "episode_number": episode,
                    "name": fname,
                    "format": ext,
                    "channel": channel_int,
                    "msg_id": int(msg_id),
                    "source_topic_id": _scan_topic_id(message),
                    "source_link": _scan_source_link(message),
                    "date_added": getattr(message, "date", None),
                    "detection_method": detection_method or "auto",
                })
                s.processed += 1
                await _update_progress()
                continue

            # ── Only process videos / video documents ─────────────
            if not _scan_is_video_message(message):
                s.skipped_nonvid += 1
                s.processed += 1
                await _update_progress()
                continue

            file = message.video or message.document
            title = message.caption or file.file_name
            msg_id = message.id
            size = get_readable_file_size(file.file_size)
            channel = str(chat_id).replace("-100", "")
            channel_int = int(channel)

            # ── Duplicate check ──────────────────────────────────
            try:
                if await _stream_id_exists(channel_int, msg_id):
                    s.skipped_dup += 1
                    s.processed += 1
                    await _update_progress()
                    continue
            except Exception as e:
                LOGGER.warning(f"[Scanner] Dup-check error msg {msg_id}: {e}")

            # ── Metadata extraction (same pipeline as receiver) ──
            try:
                metadata_info = await metadata(clean_filename(title), channel_int, msg_id)
            except Exception as e:
                LOGGER.debug(f"[Scanner] Metadata exception for msg {msg_id}: {e}")
                metadata_info = None

            if metadata_info is None:
                s.skipped_meta += 1
                s.processed += 1
                await _update_progress()
                continue

            title_clean = remove_urls(title)
            if not title_clean.endswith(('.mkv', '.mp4')):
                title_clean += '.mkv'

            # ── Insert into DB ───────────────────────────────────
            metadata_info["source_chat_id"] = int(channel_int)
            metadata_info["source_topic_id"] = _scan_topic_id(message)
            metadata_info["source_link"] = _scan_source_link(message)
            metadata_info["date_added"] = getattr(message, "date", None)
            try:
                updated_id = await db.insert_media(
                    metadata_info,
                    channel=channel_int,
                    msg_id=msg_id,
                    size=size,
                    name=title_clean,
                )
                if updated_id:
                    s.indexed += 1
                    s.indexed_videos += 1
                    LOGGER.debug(f"[Scanner] Indexed msg {msg_id}: {title_clean}")
                else:
                    s.skipped_meta += 1
            except Exception as e:
                LOGGER.error(f"[Scanner] DB insert error msg {msg_id}: {e}")
                s.errors += 1

            s.processed += 1
            await _update_progress()

        # Track empty batches to know when to stop
        if batch_had_content:
            empty_streak = 0
        else:
            empty_streak += 1

        current += BATCH_SIZE
        # Small delay between batches
        await asyncio.sleep(RATE_LIMIT_DELAY)

    # ── Finalize split ZIP groups collected during this scan ─────
    for group in split_zip_groups.values():
        if s.cancelled:
            break
        base_name = group["base_name"]
        channel_int = int(group["channel"])
        parts_by_no = group.get("parts", {})
        part_numbers = sorted(parts_by_no)

        if not part_numbers or part_numbers[0] != 1 or part_numbers != list(range(1, part_numbers[-1] + 1)):
            LOGGER.debug("[Scanner] Split ZIP incomplete, skipped: %s parts=%s", base_name, part_numbers)
            s.skipped_meta += 1
            continue
        if len(part_numbers) < 2:
            LOGGER.debug("[Scanner] Single .zip.001 ignored: %s", base_name)
            s.skipped_nonvid += 1
            continue

        parts = [parts_by_no[n] for n in part_numbers]
        first_part = parts[0]
        try:
            if await _split_part_exists(channel_int, int(first_part["msg_id"])):
                s.skipped_dup += 1
                continue
        except Exception as e:
            LOGGER.warning("[Scanner] Split ZIP duplicate check failed for %s: %s", base_name, e)

        try:
            clean_title = clean_filename(strip_split_zip_suffix(base_name))
            metadata_info = await metadata(clean_title, channel_int, int(first_part["msg_id"]))
            if metadata_info is None:
                s.skipped_meta += 1
                LOGGER.debug("[Scanner] Split ZIP metadata failed: %s", base_name)
                continue

            encoded_string = await encode_string({
                "type": "split_zip",
                "archive_name": base_name,
                "title": strip_split_zip_suffix(base_name),
                "parts": _json_safe(parts),
            })
            metadata_info["encoded_string"] = encoded_string
            metadata_info["source_type"] = "split_zip"
            metadata_info["archive_name"] = base_name
            metadata_info["part_count"] = len(parts)
            metadata_info["parts"] = parts
            metadata_info["source_chat_id"] = int(channel_int)
            metadata_info["source_topic_id"] = first_part.get("source_topic_id")
            metadata_info["source_link"] = first_part.get("source_link") or f"https://t.me/c/{channel_int}/{int(first_part['msg_id'])}"
            metadata_info["date_added"] = first_part.get("date_added")

            total_size = sum(int(p.get("size_bytes") or 0) for p in parts)
            display_size = get_readable_file_size(total_size)
            display_name = f"{remove_urls(strip_split_zip_suffix(base_name))} [Split ZIP x{len(parts)}]"

            updated_id = await db.insert_media(
                metadata_info,
                channel=channel_int,
                msg_id=int(first_part["msg_id"]),
                size=display_size,
                name=display_name,
            )
            if updated_id:
                s.indexed += 1
                s.indexed_split_zips += 1
                LOGGER.debug("[Scanner] Indexed split ZIP %s (%s parts)", base_name, len(parts))
            else:
                s.skipped_meta += 1
        except Exception as e:
            LOGGER.error("[Scanner] Split ZIP DB insert error for %s: %s", base_name, e)
            s.errors += 1

        await _update_progress(force=True)

    # ── Finalize direct split video groups collected during this scan ──
    for group in split_file_groups.values():
        if s.cancelled:
            break
        base_name = group["base_name"]
        channel_int = int(group["channel"])
        parts_by_no = group.get("parts", {})
        part_numbers = sorted(parts_by_no)

        if not part_numbers or part_numbers[0] != 1 or part_numbers != list(range(1, part_numbers[-1] + 1)):
            LOGGER.debug("[Scanner] Split video incomplete, skipped: %s parts=%s", base_name, part_numbers)
            s.skipped_meta += 1
            continue
        if len(part_numbers) < 2:
            LOGGER.debug("[Scanner] Single split video part ignored: %s", base_name)
            s.skipped_nonvid += 1
            continue

        parts = [parts_by_no[n] for n in part_numbers]
        first_part = parts[0]
        try:
            if await _split_part_exists(channel_int, int(first_part["msg_id"])):
                s.skipped_dup += 1
                continue
        except Exception as e:
            LOGGER.warning("[Scanner] Split video duplicate check failed for %s: %s", base_name, e)

        try:
            clean_title = clean_filename(strip_split_video_suffix(base_name))
            metadata_info = await metadata(clean_title, channel_int, int(first_part["msg_id"]))
            if metadata_info is None:
                s.skipped_meta += 1
                LOGGER.debug("[Scanner] Split video metadata failed: %s", base_name)
                continue

            encoded_string = await encode_string({
                "type": "split_file",
                "file_name": base_name,
                "title": strip_split_video_suffix(base_name),
                "parts": _json_safe(parts),
            })
            metadata_info["encoded_string"] = encoded_string
            metadata_info["source_type"] = "split_file"
            metadata_info["archive_name"] = base_name
            metadata_info["part_count"] = len(parts)
            metadata_info["parts"] = parts
            metadata_info["source_chat_id"] = int(channel_int)
            metadata_info["source_topic_id"] = first_part.get("source_topic_id")
            metadata_info["source_link"] = first_part.get("source_link") or f"https://t.me/c/{channel_int}/{int(first_part['msg_id'])}"
            metadata_info["date_added"] = first_part.get("date_added")

            total_size = sum(int(p.get("size_bytes") or 0) for p in parts)
            display_size = get_readable_file_size(total_size)
            display_name = f"{remove_urls(strip_split_video_suffix(base_name))} [Split x{len(parts)}]"

            updated_id = await db.insert_media(
                metadata_info,
                channel=channel_int,
                msg_id=int(first_part["msg_id"]),
                size=display_size,
                name=display_name,
            )
            if updated_id:
                s.indexed += 1
                s.indexed_split_videos += 1
                LOGGER.debug("[Scanner] Indexed split video %s (%s parts)", base_name, len(parts))
            else:
                s.skipped_meta += 1
        except Exception as e:
            LOGGER.error("[Scanner] Split video DB insert error for %s: %s", base_name, e)
            s.errors += 1

        await _update_progress(force=True)

    # ── Finalize subtitles after all media streams are indexed ─────
    for sub in pending_subtitles:
        if s.cancelled:
            break
        try:
            subtitle_id = await encode_string({"chat_id": int(sub["channel"]), "msg_id": int(sub["msg_id"])})
            ok = await db.insert_subtitle(
                imdb_id=sub["imdb_id"],
                subtitle_id=subtitle_id,
                language=sub["language"],
                name=sub["name"],
                fmt=sub["format"],
                season_number=sub.get("season_number"),
                episode_number=sub.get("episode_number"),
                source_chat_id=int(sub["channel"]),
                source_topic_id=sub.get("source_topic_id"),
                source_link=sub.get("source_link"),
                date_added=sub.get("date_added"),
            )
            if ok:
                s.indexed += 1
                s.indexed_subtitles += 1
                season = sub.get("season_number")
                episode = sub.get("episode_number")
                ep_lbl = f" S{int(season):02d}E{int(episode):02d}" if season and episode else ""
                LOGGER.debug(
                    "[Scanner] Subtitle linked via %s: %s%s [%s] msg %s",
                    sub.get("detection_method"), sub.get("imdb_id"), ep_lbl, sub.get("language"), sub.get("msg_id")
                )
            else:
                s.skipped_meta += 1
                LOGGER.debug(
                    "[Scanner] Subtitle target not found after scan: %s [%s] msg %s",
                    sub.get("imdb_id"), sub.get("language"), sub.get("msg_id")
                )
        except Exception as e:
            LOGGER.error("[Scanner] Subtitle DB error msg %s: %s", sub.get("msg_id"), e)
            s.errors += 1
        await _update_progress(force=True)

    LOGGER.info(f"[Scanner] Finished {s.channel_name}: scanned up to ID {current}, "
                f"{s.total_found} messages found, {s.indexed} indexed")


# ── Bot Commands ─────────────────────────────────────────────────────────

@Client.on_message(filters.command('scan') & filters.private & CustomFilters.owner, group=10)
async def scan_command(client: Client, message: Message):
    """Scan AUTH_CHANNELs for existing content. Skips already-indexed messages."""
    if scan_state.running:
        await message.reply_text(
            "⚠️ A scan is already running. Use /scanstatus to check progress, "
            "or /cancelscan to abort it.",
            quote=True,
        )
        return

    # Optional: /scan -1001234567890  to scan a single channel
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        target_channels = [args[1].strip()]
    else:
        target_channels = list(Telegram.AUTH_CHANNEL)

    if not target_channels:
        await message.reply_text("❌ No AUTH_CHANNELs / topic groups configured.", quote=True)
        return

    scan_state.reset()
    scan_state.running = True
    scan_state.started_at = time.time()
    scan_state.status_msg = await message.reply_text(
        "📡 <b>Channel scan starting…</b>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        for ch_id_str in target_channels:
            if scan_state.cancelled:
                break
            try:
                ch_id = int(ch_id_str)
            except ValueError:
                LOGGER.warning(f"[Scanner] Invalid channel ID: {ch_id_str}")
                continue

            await _scan_channel(client, ch_id)

        # Final summary + automatic exact-duplicate cleanup
        s = scan_state
        dedupe_stats = {}
        if not s.cancelled:
            try:
                dedupe_stats = await db.remove_duplicate_entries(delete_old_messages=True)
            except Exception as e:
                LOGGER.warning(f"[Scanner] Post-scan duplicate cleanup failed: {e}")

        status = "🛑 Cancelled" if s.cancelled else "✅ Complete"
        LOGGER.info(
            "[Scanner] %s — total=%s | video=%s | split=%s | zip=%s | subtitles=%s | errors=%s",
            status, s.indexed, s.indexed_videos, s.indexed_split_videos,
            s.indexed_split_zips, s.indexed_subtitles, s.errors,
        )
        # Keep the user-facing scan message focused on media only.
        summary_lines = [
            f"<blockquote>📡 <b>Scan {status}</b></blockquote>",
            f"🎬 Video: <code>{s.indexed_videos}</code>",
            f"🧩 Split: <code>{s.indexed_split_videos}</code>",
            f"📦 ZIP: <code>{s.indexed_split_zips}</code>",
            f"📝 Subtitles: <code>{s.indexed_subtitles}</code>",
        ]
        if s.errors:
            summary_lines.append(f"❌ Errors: <code>{s.errors}</code>")
        summary = "\n".join(summary_lines)
        try:
            await s.status_msg.edit_text(summary, parse_mode=enums.ParseMode.HTML)
        except Exception:
            await message.reply_text(summary, parse_mode=enums.ParseMode.HTML)

        # Send a separate notification so the user gets a Telegram ping
        # (editing a message doesn't trigger a notification)
        if s.processed > 20:
            notify = (
                f"{'🛑 Scan cancelled' if s.cancelled else '✅ Scan complete'} — "
                f"{s.indexed} indexed, {s.skipped_dup} skipped, "
                f"{s.errors} errors ({s.elapsed})"
            )
            await message.reply_text(notify)

    except (ChannelPrivate, ChatAdminRequired) as e:
        await message.reply_text(
            f"❌ <b>Access denied</b> to chat.\n\n"
            f"Make sure the bot is an admin in the channel/group.\n"
            f"<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        LOGGER.error(f"[Scanner] Unexpected error: {e}")
        await message.reply_text(f"❌ Scan failed: <code>{e}</code>",
                                  parse_mode=enums.ParseMode.HTML)
    finally:
        scan_state.running = False


@Client.on_message(filters.command('rescan') & filters.private & CustomFilters.owner, group=10)
async def rescan_command(client: Client, message: Message):
    """Wipe all DB entries that belong to AUTH_CHANNELs, then do a full scan.

    This is the nuclear option — it re-indexes everything from scratch.
    """
    if scan_state.running:
        await message.reply_text(
            "⚠️ A scan is already running. Cancel it first with /cancelscan.",
            quote=True,
        )
        return

    channels = list(Telegram.AUTH_CHANNEL)
    if not channels:
        await message.reply_text("❌ No AUTH_CHANNELs / topic groups configured.", quote=True)
        return

    confirm_msg = await message.reply_text(
        "⚠️ <b>RESCAN</b> will <u>delete all existing DB entries</u> "
        "for your AUTH_CHANNELs and re-index from scratch.\n\n"
        "Send <code>/rescan confirm</code> to proceed.",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )

    args = message.text.split()
    if len(args) < 2 or args[1].lower() != "confirm":
        return

    # Purge existing entries for these channels
    purge_msg = await message.reply_text(
        "🗑 Purging existing entries…", parse_mode=enums.ParseMode.HTML
    )
    purged = 0
    for ch_id_str in channels:
        channel_int = int(ch_id_str.replace("-100", ""))
        purged += await _purge_channel_entries(channel_int)

    await purge_msg.edit_text(
        f"🗑 Purged <code>{purged}</code> stream entries. Starting full scan…",
        parse_mode=enums.ParseMode.HTML,
    )

    # Now run a normal scan
    message.text = "/scan"  # trick to reuse scan_command logic
    await scan_command(client, message)


async def _purge_channel_entries(channel_int: int) -> int:
    """Delete all movie/tv streams and subtitles that belong to this channel.

    Handles normal IDs and split ZIP/direct split-video IDs that store channel
    inside their parts array.
    """
    from Backend.helper.encrypt import decode_string

    async def belongs_to_channel(item: dict) -> bool:
        sid = item.get("id") if isinstance(item, dict) else None
        if not sid:
            return False
        try:
            decoded = await decode_string(sid)
            if int(decoded.get("chat_id", 0) or 0) == channel_int:
                return True
            for part in decoded.get("parts") or []:
                if int(part.get("chat_id", 0) or 0) == channel_int:
                    return True
        except Exception:
            pass
        for part in (item.get("parts") or []):
            try:
                if int(part.get("chat_id", 0) or 0) == channel_int:
                    return True
            except Exception:
                pass
        return False

    purged = 0
    for i in range(1, db.current_db_index + 1):
        storage = db.dbs.get(f"storage_{i}")
        if storage is None:
            continue

        # Purge movies: streams + subtitles.
        async for movie in storage["movie"].find({}):
            changed = False
            new_streams = []
            for q in movie.get("telegram", []) or []:
                if await belongs_to_channel(q):
                    purged += 1
                    changed = True
                else:
                    new_streams.append(q)
            new_subs = []
            for sub in movie.get("subtitles", []) or []:
                if await belongs_to_channel(sub):
                    purged += 1
                    changed = True
                else:
                    new_subs.append(sub)

            if changed:
                movie["telegram"] = new_streams
                movie["subtitles"] = new_subs
                if movie.get("telegram") or movie.get("subtitles"):
                    await storage["movie"].replace_one({"_id": movie["_id"]}, movie)
                else:
                    await storage["movie"].delete_one({"_id": movie["_id"]})

        # Purge TV: episode streams + subtitles.
        async for tv in storage["tv"].find({}):
            tv_changed = False
            for season in tv.get("seasons", []) or []:
                kept_episodes = []
                for episode in season.get("episodes", []) or []:
                    new_streams = []
                    for q in episode.get("telegram", []) or []:
                        if await belongs_to_channel(q):
                            purged += 1
                            tv_changed = True
                        else:
                            new_streams.append(q)
                    new_subs = []
                    for sub in episode.get("subtitles", []) or []:
                        if await belongs_to_channel(sub):
                            purged += 1
                            tv_changed = True
                        else:
                            new_subs.append(sub)
                    episode["telegram"] = new_streams
                    episode["subtitles"] = new_subs
                    if episode.get("telegram") or episode.get("subtitles"):
                        kept_episodes.append(episode)
                season["episodes"] = kept_episodes

            tv["seasons"] = [s for s in tv.get("seasons", []) if s.get("episodes")]
            if tv_changed:
                if tv.get("seasons"):
                    await storage["tv"].replace_one({"_id": tv["_id"]}, tv)
                else:
                    await storage["tv"].delete_one({"_id": tv["_id"]})

    return purged


@Client.on_message(filters.command('scanstatus') & filters.private & CustomFilters.owner, group=10)
async def scan_status_command(client: Client, message: Message):
    """Show current scan progress."""
    s = scan_state
    if not s.running:
        await message.reply_text("ℹ️ No scan is currently running.", quote=True)
        return

    text = (
        f"<blockquote>📡 <b>Scan in progress:</b> {s.channel_name}</blockquote>\n\n"
        f"⏱ Elapsed: <code>{s.elapsed}</code>\n"
        f"📨 Processed: <code>{s.processed}</code>\n"
        f"✅ Indexed: <code>{s.indexed}</code>\n"
        f"⏭ Skipped (duplicate): <code>{s.skipped_dup}</code>\n"
        f"⚠️ Skipped (metadata fail): <code>{s.skipped_meta}</code>\n"
        f"📎 Skipped (non-video): <code>{s.skipped_nonvid}</code>\n"
        f"❌ Errors: <code>{s.errors}</code>"
    )
    await message.reply_text(text, quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command('cancelscan') & filters.private & CustomFilters.owner, group=10)
async def cancel_scan_command(client: Client, message: Message):
    """Abort a running scan gracefully."""
    if not scan_state.running:
        await message.reply_text("ℹ️ No scan is currently running.", quote=True)
        return

    scan_state.cancelled = True
    await message.reply_text(
        "🛑 Scan cancellation requested. Will stop after current message.",
        quote=True,
    )
