import re
from asyncio import create_task, sleep as asleep, Queue, Lock
import Backend
from Backend.helper.task_manager import edit_message
from Backend.logger import LOGGER
from Backend import db
from Backend.config import Telegram
from Backend.helper.pyro import clean_filename, get_readable_file_size, remove_urls
from Backend.helper.metadata import metadata
from pyrogram import filters, Client
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.encrypt import encode_string
from Backend.helper.metadata import extract_default_id
from Backend.helper.split_archive import (
    split_zip_info, strip_split_zip_suffix,
    split_video_info, strip_split_video_suffix,
    is_video_filename,
)


file_queue = Queue()
db_lock = Lock()


def _log_media_name(value, limit: int = 96) -> str:
    """Return a compact, single-line title for useful container logs."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return "unnamed file"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

# ─────────────────────────────────────────────────────────────
# Subtitle detection helpers
# ─────────────────────────────────────────────────────────────

SUBTITLE_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}
# A caption may include a real subtitle file name, but ``.Sub.mkv`` and
# ``ESub.mkv`` are video releases, not subtitle documents. The extension must
# be the actual end of a filename (or be followed by caption punctuation).
_SUBTITLE_NAME_RE = re.compile(
    r"(?i)\.(?P<ext>srt|vtt|ass|ssa|sub)(?=$|[\s\]\)\}>,;:!?])"
)
SUBTITLE_MIMES = {
    "text/x-subrip",
    "application/x-subrip",
    "text/vtt",
    "text/plain",
    "application/octet-stream",
}

# Caption pattern:  [SUB:tt1234567 en]  or  [SUB:tt1234567 S01E02 en]
_SUB_RE = re.compile(
    r"\[SUB:(?P<imdb>tt\d+)"
    r"(?:\s+S(?P<season>\d+)E(?P<episode>\d+))?"
    r"\s+(?P<lang>[a-zA-Z]{2,3})\]",
    re.IGNORECASE,
)

LANG_NAMES = {
    "en": "English", "si": "Sinhala", "ta": "Tamil", "hi": "Hindi",
    "fr": "French", "de": "German", "es": "Spanish", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "pt": "Portuguese",
    "ru": "Russian", "it": "Italian", "nl": "Dutch", "tr": "Turkish",
    "te": "Telugu", "ml": "Malayalam", "kn": "Kannada",
}

# Subtitle language detector. Uses explicit subtitle language keywords first.
# This prevents content language names from winning over the actual subtitle
# language, e.g. "Malayalam movie Sinhala sub" -> si.
_LANG_REGEX_MAP = [
    (r"(?i)(සිංහල|sinhalese|sinhala|sinhala[_\s-]*sub|\bsi\b|\bsin\b)", "si"),
    (r"(?i)(english|eng[_\s-]*sub|\beng\b|\ben\b|\besub\b|e[_\s-]*sub)", "en"),
    (r"(?i)(தமிழ்|tamil[_\s-]*sub|\btamil\b|\btam\b|\bta\b)", "ta"),
    (r"(?i)(hindi|\bhi\b)", "hi"),
    (r"(?i)(telugu|\bte\b)", "te"),
    (r"(?i)(malayalam|\bml\b)", "ml"),
    (r"(?i)(kannada|\bkn\b)", "kn"),
    (r"(?i)(french|\bfr\b)", "fr"),
    (r"(?i)(german|\bde\b)", "de"),
    (r"(?i)(spanish|\bes\b)", "es"),
    (r"(?i)(japanese|\bja\b)", "ja"),
    (r"(?i)(korean|\bko\b)", "ko"),
    (r"(?i)(chinese|\bzh\b)", "zh"),
    # Arabic subtitle detection: English names/codes + Arabic script.
    # Examples: Arabic, ArabSub, AR Sub, ara, ar, عربي, عربى, العربية, ترجمة عربية
    (r"(?i)(arabic[_\s-]*sub|arab[_\s-]*sub|arabic|\bara\b|\bar\b|عربي|عربى|العربية|ترجمة[_\s-]*عربية|ترجمه[_\s-]*عربي)", "ar"),
]


def _detect_language_from_text(text: str) -> str | None:
    """Return a subtitle language code found in text, or None when unknown."""
    if not text:
        return None
    normalized = re.sub(r"[._\-]+", " ", text)
    for pattern, code in _LANG_REGEX_MAP:
        if re.search(pattern, normalized):
            return code
    return None


def _detect_language(fname: str) -> str:
    """Backward-compatible language detector with English fallback."""
    return _detect_language_from_text(fname) or "en"


def _detect_subtitle_language(caption: str, fname: str) -> str:
    """Detect subtitle language from caption first, then filename.

    Supports Sinhala/English/Tamil/Arabic badges such as si/en/ta/ar, Sinhala/Arabic script,
    ESub/EngSub, and normal language names. Caption wins over filename.
    """
    return _detect_language_from_text(caption or "") or _detect_language_from_text(fname or "") or "en"


def _clean_caption_for_detection(caption: str) -> str:
    """Turn a Telegram caption into a safe title candidate for metadata lookup."""
    if not caption:
        return ""
    text = remove_urls(caption)
    text = _SUB_RE.sub(" ", text)
    # Remove common upload/source labels without destroying title/year tokens.
    text = re.sub(r"(?i)\b(source|file|filename|download|subtitle|subtitles)\b\s*[:：-]?", " ", text)
    text = re.sub(r"[`*_~>|#]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def _detect_subtitle_metadata(caption: str, fname: str, channel: int, msg_id: int):
    """Detect subtitle target using caption first, then filename.

    Returns (metadata_info, detection_method, source_text).
    detection_method is one of: caption, filename, or None.
    """
    candidates: list[tuple[str, str]] = []

    caption_query = _clean_caption_for_detection(caption)
    if caption_query:
        candidates.append(("caption", caption_query))

    if fname:
        candidates.append(("filename", fname))

    seen: set[str] = set()
    for method, source in candidates:
        cleaned = clean_filename(source)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        try:
            meta = await metadata(cleaned, int(channel), msg_id, require_quality=False)
        except Exception as e:
            LOGGER.warning(f"Subtitle metadata error from {method} for msg {msg_id}: {e}")
            meta = None
        if meta and meta.get("imdb_id"):
            return meta, method, source

    return None, None, None


def _subtitle_extension_from_text(value: str | None) -> str:
    """Return a real subtitle extension from a filename/caption line.

    This intentionally rejects a subtitle *label* embedded inside a video name
    such as ``Movie.1080p.ESub.mkv`` or ``Movie.Sub.mkv``.
    """
    match = _SUBTITLE_NAME_RE.search(str(value or "").strip())
    return f".{match.group('ext').lower()}" if match else ""


def _is_subtitle(document) -> bool:
    """Return True only for a document whose actual file extension is subtitle."""
    if document is None:
        return False
    fname = (getattr(document, "file_name", "") or "").strip()
    # Telegram can label many binary files as application/octet-stream, so the
    # exact filename extension is the reliable source of truth here.
    return _subtitle_extension_from_text(fname) in SUBTITLE_EXTS


def _json_safe(value):
    """Convert Telegram/Python objects to JSON-safe values for encoded stream IDs.

    Split ZIP/video parts can include Telegram message dates as ``datetime`` objects.
    ``encode_string`` JSON-serializes its payload, so nested datetimes must become
    ISO strings first. This helper is intentionally recursive and conservative.
    """
    from datetime import datetime, date

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _subtitle_candidate_name(message: Message) -> str:
    """Return the best subtitle filename/candidate from a Telegram message.

    Some Telegram documents arrive with a generic filename, while the real
    ``Movie.Name.srt`` / ``Movie.Name.vtt`` is in the caption. For rescan and live
    uploads we prefer any caption line that contains a subtitle extension.
    """
    doc = getattr(message, "document", None)
    fname = (getattr(doc, "file_name", "") or "").strip()
    caption = (getattr(message, "caption", "") or "").strip()

    candidates = []
    for value in (caption, fname):
        if not value:
            continue
        # Try every line first, because captions often contain labels and a file line.
        for line in str(value).splitlines():
            line = line.strip().strip("`*_")
            if line:
                candidates.append(line)

    for candidate in candidates:
        match = _SUBTITLE_NAME_RE.search(candidate)
        if match:
            # Remove simple labels like "File:" and trim trailing caption text
            # while keeping the real subtitle filename only.
            candidate = re.sub(r"(?i)^\s*(file|filename|subtitle|sub)\s*[:：-]\s*", "", candidate).strip()
            match = _SUBTITLE_NAME_RE.search(candidate)
            return candidate[:match.end()].strip() if match else candidate

    return fname or caption or "subtitle.srt"


def _is_subtitle_message(message: Message) -> bool:
    """Return True when a Telegram message should be treated as a subtitle.

    Unlike ``_is_subtitle(document)``, this also checks the caption. That makes
    `/scan` and `/rescan confirm` work for old files where Telegram's document
    filename is generic but the caption contains the actual `.srt/.vtt/.ass` name.
    """
    doc = getattr(message, "document", None)
    if not doc:
        return False
    if _is_subtitle(doc):
        return True

    candidate = _subtitle_candidate_name(message)
    return _subtitle_extension_from_text(candidate) in SUBTITLE_EXTS


def _parse_sub_caption(caption: str):
    """Parse [SUB:tt1234567 S01E02 en] from a caption.
    Returns (imdb_id, lang, season_or_None, episode_or_None) or None.
    """
    if not caption:
        return None
    m = _SUB_RE.search(caption)
    if not m:
        return None
    imdb  = m.group("imdb").lower()
    lang  = m.group("lang").lower()
    sea   = int(m.group("season"))   if m.group("season")  else None
    ep    = int(m.group("episode"))  if m.group("episode") else None
    return imdb, lang, sea, ep


# ─────────────────────────────────────────────────────────────
# Telegram source helpers: channels + forum topic groups
# ─────────────────────────────────────────────────────────────

def _configured_source_ids() -> set[str]:
    """Return configured source IDs in one canonical form.

    WebUI values are normally strings, while the legacy source-manager document
    may contain integers.  Normalising here prevents a valid live update from
    being rejected only because the stored type differs.
    """
    sources: set[str] = set()
    for value in getattr(Telegram, "AUTH_CHANNEL", []) or []:
        try:
            sources.add(str(int(str(value).strip())))
        except (TypeError, ValueError):
            continue
    return sources


def _is_auth_source(message: Message) -> bool:
    """Return True if the message comes from an allowed channel/supergroup.

    Telegram forum topics are not separate channels. They are messages inside
    one supergroup, so AUTH_CHANNEL must contain the parent supergroup ID, e.g.
    -1003586810234. All topics inside that group are then accepted.
    """
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    try:
        return str(int(chat_id)) in _configured_source_ids()
    except (TypeError, ValueError):
        return False


def _internal_chat_id(chat_id) -> str:
    """Convert -1001234567890 -> 1234567890 for t.me/c links."""
    return str(chat_id).replace("-100", "", 1)


def _topic_id(message: Message) -> int | None:
    """Best-effort forum topic/thread id for Telegram topic groups."""
    for attr in ("message_thread_id", "reply_to_top_message_id"):
        val = getattr(message, attr, None)
        if val:
            try:
                return int(val)
            except Exception:
                pass
    reply = getattr(message, "reply_to_message", None)
    if reply:
        for attr in ("id", "message_id"):
            val = getattr(reply, attr, None)
            if val:
                try:
                    return int(val)
                except Exception:
                    pass
    return None


def _source_link(message: Message) -> str:
    """Make a clickable source link for normal channels and topic groups.

    Normal/private channel: https://t.me/c/<chat>/<msg>
    Forum topic group:     https://t.me/c/<chat>/<topic>/<msg>
    """
    internal = _internal_chat_id(message.chat.id)
    msg_id = int(message.id)
    topic = _topic_id(message)
    if topic and topic != msg_id:
        return f"https://t.me/c/{internal}/{topic}/{msg_id}"
    return f"https://t.me/c/{internal}/{msg_id}"


def _media_name_candidates(message: Message) -> list[str]:
    """Return possible filenames from Telegram media + caption.

    Split parts are sometimes uploaded with a generic filename while the real
    ``Movie.mkv.001`` / ``Movie.zip.001`` text is only in the caption.
    """
    values = []
    media = getattr(message, "document", None) or getattr(message, "video", None)
    if media is not None:
        values.append(getattr(media, "file_name", None))
    values.append(getattr(message, "caption", None))

    out, seen = [], set()
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


def _first_split_zip_info(message: Message):
    for candidate in _media_name_candidates(message):
        info = split_zip_info(candidate)
        if info:
            return info, candidate
    return None, None


def _first_split_video_info(message: Message):
    for candidate in _media_name_candidates(message):
        info = split_video_info(candidate)
        if info:
            return info, candidate
    return None, None


def _is_video_message(message: Message) -> bool:
    """Recognize Telegram videos sent either as video media or as documents.

    Some Android/Telegram clients upload MKV files as documents with
    ``application/octet-stream``.  Their filename is still valid evidence, so
    use a supported video extension as a fallback after the MIME check.
    """
    if getattr(message, "video", None):
        return True

    document = getattr(message, "document", None)
    if not document:
        return False

    mime = (getattr(document, "mime_type", "") or "").lower()
    if mime.startswith("video/"):
        return True

    return is_video_filename(getattr(document, "file_name", ""))


# ─────────────────────────────────────────────────────────────
# Video processing queue
# ─────────────────────────────────────────────────────────────

async def process_file():
    while True:
        metadata_info, channel, msg_id, size, title = await file_queue.get()
        try:
            async with db_lock:
                updated_id = await db.insert_media(metadata_info, channel=channel, msg_id=msg_id, size=size, name=title)
                if updated_id:
                    media_kind = str(metadata_info.get("media_type") or "media").replace("_", " ").title()
                    LOGGER.info("✅ %s indexed: %s", media_kind, _log_media_name(title))
                else:
                    LOGGER.warning("⚠️ Media not indexed: %s", _log_media_name(title))
        except Exception as exc:
            LOGGER.exception("❌ Media queue error for %s: %s", _log_media_name(title), exc)
        finally:
            file_queue.task_done()

for _ in range(1):
    create_task(process_file())



# ─────────────────────────────────────────────────────────────
# Split ZIP grouping: name.zip.001, name.zip.002, ...
# ─────────────────────────────────────────────────────────────

split_zip_groups = {}
split_zip_tasks = {}
split_zip_lock = Lock()


def _split_zip_group_key(channel: str, base_name: str) -> str:
    return f"{channel}:{base_name.lower()}"


async def _finalize_split_zip_group(key: str):
    await asleep(getattr(Telegram, "SPLIT_ZIP_FINALIZE_SECONDS", 60))

    async with split_zip_lock:
        group = split_zip_groups.pop(key, None)
        split_zip_tasks.pop(key, None)

    if not group:
        return

    channel = int(group["channel"])
    base_name = group["base_name"]
    parts_by_no = group.get("parts", {})
    part_numbers = sorted(parts_by_no)

    if not part_numbers or part_numbers[0] != 1 or part_numbers != list(range(1, part_numbers[-1] + 1)):
        LOGGER.debug(
            "Split ZIP not indexed because parts are incomplete: %s parts=%s",
            base_name, part_numbers,
        )
        return

    if len(part_numbers) < 2:
        LOGGER.debug("Single .zip.001 ignored, not a multipart ZIP: %s", base_name)
        return

    parts = [parts_by_no[n] for n in part_numbers]
    first_part = parts[0]
    clean_title = clean_filename(strip_split_zip_suffix(base_name))

    try:
        metadata_info = await metadata(clean_title, channel, int(first_part["msg_id"]))
        if metadata_info is None:
            LOGGER.debug("Metadata failed for split ZIP archive: %s", base_name)
            return

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
        metadata_info["source_chat_id"] = int(channel)
        metadata_info["source_topic_id"] = first_part.get("source_topic_id")
        metadata_info["source_link"] = first_part.get("source_link") or f"https://t.me/c/{channel}/{int(first_part['msg_id'])}"
        metadata_info["date_added"] = first_part.get("date_added")

        total_size = sum(int(p.get("size_bytes") or 0) for p in parts)
        display_size = get_readable_file_size(total_size)
        display_name = f"{remove_urls(strip_split_zip_suffix(base_name))} [Split ZIP x{len(parts)}]"

        await file_queue.put((metadata_info, channel, int(first_part["msg_id"]), display_size, display_name))
        LOGGER.info("📦 Split ZIP complete: %s (%s parts) — queued", _log_media_name(base_name), len(parts))
    except Exception as e:
        LOGGER.exception("Failed to finalize split ZIP archive %s: %s", base_name, e)


async def _handle_split_zip_part(client: Client, message: Message, info: dict):
    file = message.document
    fname = file.file_name or message.caption or "split.zip.001"
    channel = str(message.chat.id).replace("-100", "")
    msg_id = message.id
    key = _split_zip_group_key(channel, info["base_name"])

    async with split_zip_lock:
        group = split_zip_groups.setdefault(key, {
            "channel": channel,
            "base_name": info["base_name"],
            "parts": {},
        })
        group["parts"][int(info["part_number"])] = {
            "chat_id": int(channel),
            "msg_id": int(msg_id),
            "part": int(info["part_number"]),
            "name": fname,
            "size_bytes": int(getattr(file, "file_size", 0) or 0),
            "date_added": getattr(message, "date", None),
            "source_topic_id": _topic_id(message),
            "source_link": _source_link(message),
        }

        old_task = split_zip_tasks.get(key)
        if old_task and not old_task.done():
            old_task.cancel()
        split_zip_tasks[key] = create_task(_finalize_split_zip_group(key))

    LOGGER.info("📦 Split ZIP part %03d received: %s", int(info["part_number"]), _log_media_name(info["base_name"]))


# ─────────────────────────────────────────────────────────────
# Direct split video grouping: name.mkv.001, name.mkv.002, ...
# This is the smoothest low-RAM method because no ZIP metadata/decompression is needed.
# ─────────────────────────────────────────────────────────────

split_file_groups = {}
split_file_tasks = {}
split_file_lock = Lock()


def _split_file_group_key(channel: str, base_name: str) -> str:
    return f"{channel}:{base_name.lower()}"


async def _finalize_split_file_group(key: str):
    await asleep(getattr(Telegram, "SPLIT_ZIP_FINALIZE_SECONDS", 60))

    async with split_file_lock:
        group = split_file_groups.pop(key, None)
        split_file_tasks.pop(key, None)

    if not group:
        return

    channel = int(group["channel"])
    base_name = group["base_name"]
    parts_by_no = group.get("parts", {})
    part_numbers = sorted(parts_by_no)

    if not part_numbers or part_numbers[0] != 1 or part_numbers != list(range(1, part_numbers[-1] + 1)):
        LOGGER.debug(
            "Split video not indexed because parts are incomplete: %s parts=%s",
            base_name, part_numbers,
        )
        return

    if len(part_numbers) < 2:
        LOGGER.debug("Single split video part ignored, not multipart: %s", base_name)
        return

    parts = [parts_by_no[n] for n in part_numbers]
    first_part = parts[0]
    clean_title = clean_filename(strip_split_video_suffix(base_name))

    try:
        metadata_info = await metadata(clean_title, channel, int(first_part["msg_id"]))
        if metadata_info is None:
            LOGGER.debug("Metadata failed for split video: %s", base_name)
            return

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
        metadata_info["source_chat_id"] = int(channel)
        metadata_info["source_topic_id"] = first_part.get("source_topic_id")
        metadata_info["source_link"] = first_part.get("source_link") or f"https://t.me/c/{channel}/{int(first_part['msg_id'])}"
        metadata_info["date_added"] = first_part.get("date_added")

        total_size = sum(int(p.get("size_bytes") or 0) for p in parts)
        display_size = get_readable_file_size(total_size)
        display_name = f"{remove_urls(strip_split_video_suffix(base_name))} [Split x{len(parts)}]"

        await file_queue.put((metadata_info, channel, int(first_part["msg_id"]), display_size, display_name))
        LOGGER.info("🧩 Split video complete: %s (%s parts) — queued", _log_media_name(base_name), len(parts))
    except Exception as e:
        LOGGER.exception("Failed to finalize split video %s: %s", base_name, e)


async def _handle_split_file_part(client: Client, message: Message, info: dict):
    file = message.document or message.video
    fname = getattr(file, "file_name", None) or message.caption or "video.mkv.001"
    channel = str(message.chat.id).replace("-100", "")
    msg_id = message.id
    key = _split_file_group_key(channel, info["base_name"])

    async with split_file_lock:
        group = split_file_groups.setdefault(key, {
            "channel": channel,
            "base_name": info["base_name"],
            "parts": {},
        })
        group["parts"][int(info["part_number"])] = {
            "chat_id": int(channel),
            "msg_id": int(msg_id),
            "part": int(info["part_number"]),
            "name": fname,
            "size_bytes": int(getattr(file, "file_size", 0) or 0),
            "date_added": getattr(message, "date", None),
            "source_topic_id": _topic_id(message),
            "source_link": _source_link(message),
        }

        old_task = split_file_tasks.get(key)
        if old_task and not old_task.done():
            old_task.cancel()
        split_file_tasks[key] = create_task(_finalize_split_file_group(key))

    LOGGER.info("🧩 Split video part %03d received: %s", int(info["part_number"]), _log_media_name(info["base_name"]))

# ─────────────────────────────────────────────────────────────
# Live update receiver binding
# ─────────────────────────────────────────────────────────────

async def bind_live_receiver(client: Client) -> None:
    """Bind the live uploader receiver directly to the primary bot client.

    This intentionally does not depend on PyroFork plugin discovery.  The
    scanner can import this module later, but the real-time handler must be
    attached before the primary bot begins consuming Telegram updates.
    """
    if getattr(client, "_tgstremio_live_receiver_bound", False):
        return

    client.add_handler(MessageHandler(file_receive_handler, filters.all), group=-100)
    setattr(client, "_tgstremio_live_receiver_bound", True)

    # Client.add_handler schedules its registration internally. Yield once so
    # the handler is present before Telegram update processing begins.
    await asleep(0)
    LOGGER.info(
        "📡 Live upload receiver bound to @%s | configured sources: %s",
        getattr(client, "username", None) or getattr(getattr(client, "me", None), "username", None) or "bot",
        ", ".join(sorted(_configured_source_ids())) or "none",
    )


async def verify_live_source_access(client: Client) -> None:
    """Log whether the primary bot can receive posts from every source.

    A rescan pulls history on demand. Live indexing needs Telegram to *push* an
    update to the primary bot, which requires the bot to be in the exact source
    chat and normally be an administrator. The startup log makes that difference
    visible instead of silently skipping every new upload.
    """
    source_ids = sorted(_configured_source_ids())
    if not source_ids:
        LOGGER.warning("⚠️ Live upload receiver has no configured source channels/groups.")
        return

    bot_id = getattr(getattr(client, "me", None), "id", None)
    for source_id in source_ids:
        try:
            chat = await client.get_chat(int(source_id))
            chat_name = getattr(chat, "title", None) or getattr(chat, "username", None) or source_id
            member = await client.get_chat_member(int(source_id), bot_id) if bot_id else None
            status = str(getattr(member, "status", "unknown")).lower()
            LOGGER.info(
                "📡 Live source verified: %s (%s) | primary bot role=%s",
                chat_name, source_id, status,
            )
            if "administrator" not in status and "owner" not in status:
                LOGGER.warning(
                    "⚠️ Live updates may not arrive from %s. Make the primary bot an admin in this exact channel/group.",
                    source_id,
                )
        except Exception as exc:
            LOGGER.warning(
                "⚠️ Cannot verify live source %s for the primary bot: %s. "
                "Add @%s as an admin to this exact source chat.",
                source_id,
                exc,
                getattr(client, "username", None) or "your main bot",
            )


# ─────────────────────────────────────────────────────────────
# Video handler
# ─────────────────────────────────────────────────────────────

async def file_receive_handler(client: Client, message: Message):
    """Index a new source upload as soon as Telegram delivers its update.

    Do not rely on ``filters.channel`` / ``filters.group`` here.  Some PyroFork
    versions classify channel posts and forum-topic documents inconsistently,
    which can silently prevent the handler from ever running.  We receive the
    update first, then make the source and media checks ourselves.
    """
    if not (getattr(message, "document", None) or getattr(message, "video", None)):
        return

    source_allowed = _is_auth_source(message)
    if not source_allowed:
        LOGGER.warning(
            "⚠️ Live media received from unconfigured chat %s; expected source IDs: %s",
            getattr(getattr(message, "chat", None), "id", None),
            ", ".join(sorted(_configured_source_ids())) or "none",
        )
        return

    file_obj = getattr(message, "document", None) or getattr(message, "video", None)
    live_name = getattr(file_obj, "file_name", None) or getattr(message, "caption", None) or "unnamed file"
    LOGGER.info(
        "📥 Live media received: chat=%s message=%s file=%s",
        getattr(getattr(message, "chat", None), "id", None),
        getattr(message, "id", None),
        _log_media_name(live_name),
    )

    try:
        # ── Subtitle file ─────────────────────────────────────
        if message.document and _is_subtitle_message(message):
            await _handle_subtitle(client, message)
            return

        # ── Split ZIP archive part (.zip.001, .zip.002, ...) ──
        if getattr(Telegram, "SPLIT_ZIP_STREAM", True) and message.document:
            split_info, _split_name = _first_split_zip_info(message)
            if split_info:
                await _handle_split_zip_part(client, message, split_info)
                return

        # ── Direct split video part (.mkv.001, .mp4.002, ...) ──
        if message.document:
            split_file_info, _split_name = _first_split_video_info(message)
            if split_file_info:
                await _handle_split_file_part(client, message, split_file_info)
                return

        # ── Video file ────────────────────────────────────────
        if _is_video_message(message):
            file = message.video or message.document
            title = message.caption or file.file_name
            msg_id = message.id
            size = get_readable_file_size(file.file_size)
            channel = str(message.chat.id).replace("-100", "")

            metadata_info = await metadata(clean_filename(title), int(channel), msg_id)
            if metadata_info is None:
                LOGGER.warning("⚠️ Video skipped — metadata not found: %s", _log_media_name(title))
                return

            title = remove_urls(title)
            if not title.endswith(('.mkv', '.mp4')):
                title += '.mkv'

            if Backend.USE_DEFAULT_ID:
                new_caption = (message.caption + "\n\n" + Backend.USE_DEFAULT_ID) if message.caption else Backend.USE_DEFAULT_ID
                create_task(edit_message(
                    chat_id=message.chat.id,
                    msg_id=message.id,
                    new_caption=new_caption
                ))

            metadata_info["source_chat_id"] = int(channel)
            metadata_info["source_topic_id"] = _topic_id(message)
            metadata_info["source_link"] = _source_link(message)
            metadata_info["date_added"] = getattr(message, "date", None)
            await file_queue.put((metadata_info, int(channel), msg_id, size, title))
            LOGGER.info("🎬 Video queued: %s (%s)", _log_media_name(title), size)
        else:
            # An authorized document can still be a poster, NFO, or another
            # non-media attachment.  Keep source channels clean and simply log it.
            LOGGER.info("Ignored non-video document: %s", _log_media_name(live_name))

    except FloodWait as e:
        LOGGER.info(f"Sleeping for {str(e.value)}s")
        await asleep(e.value)
        await message.reply_text(
            text=f"Got Floodwait of {str(e.value)}s",
            disable_web_page_preview=True,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as exc:
        LOGGER.exception(
            "Live media handler failed: chat=%s message=%s file=%s error=%s",
            getattr(getattr(message, "chat", None), "id", None),
            getattr(message, "id", None),
            _log_media_name(live_name),
            exc,
        )


# ─────────────────────────────────────────────────────────────
# Subtitle handler
# ─────────────────────────────────────────────────────────────

async def _handle_subtitle(client: Client, message: Message):
    """
    Process a subtitle document sent in the channel.

    Priority:
      1. Explicit caption  →  [SUB:tt1234567 en]  /  [SUB:tt1234567 S01E02 en]
      2. Auto-detect       →  parse Telegram caption first
      3. Fallback          →  parse the real file name if caption is empty/unknown

    All status messages (success/failure) go to the owner's DM rather than
    being posted as a reply in the channel -- the channel is meant to stay
    files-only, same as the video path (which never posts anything back).
    """
    doc     = message.document
    caption = message.caption or ""
    fname   = _subtitle_candidate_name(message)
    ext     = (_subtitle_extension_from_text(fname) or ".srt").lstrip(".")
    channel = str(message.chat.id).replace("-100", "")
    msg_id  = message.id
    source_link = f"🔗 [Source]({_source_link(message)})\n\n"

    # ── 1. Try explicit [SUB:...] caption ────────────────────
    parsed = _parse_sub_caption(caption)

    if parsed:
        imdb_id, lang, season, episode = parsed
        detection_method = "caption"

    else:
        # ── 2. Auto-detect from caption first, then filename ─────
        metadata_info, detection_method, detected_source = await _detect_subtitle_metadata(
            caption, fname, int(channel), msg_id
        )

        if metadata_info is None:
            await client.send_message(
                Telegram.OWNER_ID,
                source_link +
                "⚠️ **Could not auto-detect movie/show from caption or filename.**\n\n"
                f"📝 Caption: `{caption or 'empty'}`\n"
                f"📄 File: `{fname}`\n\n"
                "Use a manual caption:\n"
                "`[SUB:tt1234567 en]`\n"
                "or for a TV episode:\n"
                "`[SUB:tt1234567 S01E02 en]`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        imdb_id = metadata_info.get("imdb_id")
        if not imdb_id:
            await client.send_message(
                Telegram.OWNER_ID,
                source_link +
                "⚠️ **Movie found on TMDB but has no IMDB ID.**\n\n"
                f"📝 Caption: `{caption or 'empty'}`\n"
                f"📄 File: `{fname}`\n\n"
                "Add the caption manually:\n`[SUB:tt1234567 en]`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Detect season/episode from metadata (for TV shows)
        season  = metadata_info.get("season_number") or None
        episode = metadata_info.get("episode_number") or None
        # Detect language from caption first, then filename keywords
        lang    = _detect_subtitle_language(caption, fname)

    # ── Link subtitle to DB ───────────────────────────────────
    subtitle_id = await encode_string({"chat_id": int(channel), "msg_id": msg_id})

    success = await db.insert_subtitle(
        imdb_id=imdb_id,
        subtitle_id=subtitle_id,
        language=lang,
        name=fname,
        fmt=ext,
        season_number=season,
        episode_number=episode,
        source_chat_id=int(channel),
        source_topic_id=_topic_id(message),
        source_link=_source_link(message),
        date_added=getattr(message, "date", None),
    )

    # If media not indexed yet, retry a few times (e.g. subtitle sent before video)
    if not success:
        for attempt in range(3):
            await asleep(5)
            success = await db.insert_subtitle(
                imdb_id=imdb_id,
                subtitle_id=subtitle_id,
                language=lang,
                name=fname,
                fmt=ext,
                season_number=season,
                episode_number=episode,
                source_chat_id=int(channel),
                source_topic_id=_topic_id(message),
                source_link=_source_link(message),
                date_added=getattr(message, "date", None),
            )
            if success:
                LOGGER.debug(f"Subtitle linked on retry {attempt + 1}: {imdb_id}")
                break

    lang_label = LANG_NAMES.get(lang, lang.upper())
    ep_label   = f" S{season:02d}E{episode:02d}" if season and episode else ""
    if detection_method == "caption":
        method_tag = "✏️ Caption"
    elif detection_method == "filename":
        method_tag = "📄 Filename"
    else:
        method_tag = "🤖 Auto-detected"

    if success:
        LOGGER.info("📝 Subtitle indexed: %s%s [%s] — %s", imdb_id, ep_label, lang, _log_media_name(fname))
        await client.send_message(
            Telegram.OWNER_ID,
            source_link +
            f"✅ **Subtitle linked!**\n\n"
            f"🎬 IMDB: `{imdb_id}`{ep_label}\n"
            f"🌐 Language: **{lang_label}**\n"
            f"📄 File: `{fname}`\n"
            f"{method_tag} used as first match",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        LOGGER.warning("⚠️ Subtitle waiting for matching media: %s [%s]", _log_media_name(fname), lang)
        await client.send_message(
            Telegram.OWNER_ID,
            source_link +
            f"❌ Could not find `{imdb_id}` in the database.\n"
            "Make sure the movie/series video is indexed first, then re-send the subtitle.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ─────────────────────────────────────────────────────────────
# Edited message handler
# ─────────────────────────────────────────────────────────────

@Client.on_edited_message((filters.channel | filters.group) & (filters.document | filters.video))
async def file_edited_handler(client: Client, message: Message):
    if not _is_auth_source(message):
        return
    try:
        if _is_video_message(message):
            file = message.video or message.document
            title = message.caption or file.file_name
            msg_id = message.id
            size = get_readable_file_size(file.file_size)
            channel = str(message.chat.id).replace("-100", "")

            override_id = extract_default_id(message.caption) if message.caption else None

            if override_id:
                LOGGER.info(f"Detected override ID '{override_id}' in edited message {msg_id}")
                stream_id_hash = await encode_string({"chat_id": int(channel), "msg_id": msg_id})
                await db.delete_media_by_stream_id(stream_id_hash)

                metadata_info = await metadata(clean_filename(title), int(channel), msg_id, override_id=override_id)
                if metadata_info is None:
                    LOGGER.warning(f"Metadata failed for edited file: {title} (ID: {msg_id})")
                    return

                title = remove_urls(title)
                if not title.endswith(('.mkv', '.mp4')):
                    title += '.mkv'

                metadata_info["source_chat_id"] = int(channel)
                metadata_info["source_topic_id"] = _topic_id(message)
                metadata_info["source_link"] = _source_link(message)
                await file_queue.put((metadata_info, int(channel), msg_id, size, title))
    except Exception as e:
        LOGGER.error(f"Error handling edited generic file {message.id}: {e}")


# ─────────────────────────────────────────────────────────────
# Deleted message handler
# ─────────────────────────────────────────────────────────────

@Client.on_deleted_messages(filters.channel | filters.group)
async def file_deleted_handler(client: Client, messages: list[Message]):
    """Auto-clean DB when source files are deleted from Telegram.

    Works for normal videos, subtitles, and split ZIP/direct split-video parts.
    If any part of a split upload is deleted, the whole stream quality is
    removed from Stremio so a re-send does not create dead duplicates.
    """
    try:
        for message in messages:
            try:
                if not message.chat or str(message.chat.id) not in Telegram.AUTH_CHANNEL:
                    continue
                channel = str(message.chat.id).replace("-100", "", 1)
                msg_id = int(message.id)

                deleted = await db.delete_media_by_message(int(channel), msg_id)
                if not deleted:
                    # Backward-compatible direct video cleanup.
                    stream_id_hash = await encode_string({"chat_id": int(channel), "msg_id": msg_id})
                    deleted = await db.delete_media_by_stream_id(stream_id_hash)

                if deleted:
                    LOGGER.debug(f"Automatically purged deleted Telegram message {msg_id} from database.")
            except Exception as ex:
                LOGGER.error(f"Failed to scrub deleted message {getattr(message, 'id', '?')}: {ex}")
    except Exception as e:
        LOGGER.error(f"Error handling deleted messages: {e}")
