import re
from asyncio import create_task, sleep as asleep, Queue, Lock
import difflib
import Backend
from Backend.helper.task_manager import edit_message
from Backend.logger import LOGGER
from Backend import db
from Backend.config import Telegram
from Backend.helper.pyro import clean_filename, get_readable_file_size, remove_urls
from Backend.helper.metadata import metadata
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.encrypt import encode_string
from Backend.helper.metadata import extract_default_id


file_queue = Queue()
db_lock = Lock()

# ─────────────────────────────────────────────────────────────
# Subtitle detection helpers
# ─────────────────────────────────────────────────────────────

SUBTITLE_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}
SUBTITLE_MIMES = {
    "text/x-subrip",
    "application/x-subrip",
    "text/vtt",
    "text/plain",
    "application/octet-stream",
}

# Caption pattern:  [SUB:tt1234567 en]  or  [SUB:tt1234567 S01E02 en]
_SUB_RE = re.compile(
    r"[SUB:(?P<imdb>ttd+)"
    r"(?:s+S(?P<season>d+)E(?P<episode>d+))?"
    r"s+(?P<lang>[a-zA-Z]{2,3})]",
    re.IGNORECASE,
)

LANG_NAMES = {
    "en": "English", "si": "Sinhala", "ta": "Tamil", "hi": "Hindi",
    "fr": "French", "de": "German", "es": "Spanish", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "pt": "Portuguese",
    "ru": "Russian", "it": "Italian", "nl": "Dutch", "tr": "Turkish",
    "te": "Telugu", "ml": "Malayalam", "kn": "Kannada",
}

# Priority-ordered keyword → ISO-639-1 mapping.
# "ESub" (English subtitle embedded in foreign film) checked before content languages.
_LANG_KEYWORD_MAP = [
    ("esub",      "en"),
    ("english",   "en"),
    ("sinhala",   "si"),
    ("sinhalese", "si"),
    ("hindi",     "hi"),
    ("telugu",    "te"),
    ("malayalam", "ml"),
    ("kannada",   "kn"),
    ("french",    "fr"),
    ("german",    "de"),
    ("spanish",   "es"),
    ("japanese",  "ja"),
    ("korean",    "ko"),
    ("chinese",   "zh"),
    ("arabic",    "ar"),
    ("portuguese","pt"),
    ("russian",   "ru"),
    ("italian",   "it"),
    ("dutch",     "nl"),
    ("turkish",   "tr"),
    ("tamil",     "ta"),
]


def _detect_language(fname: str) -> str:
    """Auto-detect subtitle language from filename keywords."""
    lower = fname.lower()
    for keyword, code in _LANG_KEYWORD_MAP:
        if keyword in lower:
            return code
    return "en"


def _is_subtitle(document) -> bool:
    """Return True if this document looks like a subtitle file."""
    if document is None:
        return False
    mime = (getattr(document, "mime_type", "") or "").lower()
    fname = getattr(document, "file_name", "") or ""
    ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    return ext in SUBTITLE_EXTS or (mime in SUBTITLE_MIMES and ext in SUBTITLE_EXTS)


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


async def _find_matching_media_by_filename(subtitle_fname: str, channel: int) -> tuple:
    """
    Find a video in the database with a similar filename to the subtitle.
    Uses difflib.SequenceMatcher with 60% similarity threshold.
    Returns (imdb_id, season, episode, similarity_score) or (None, None, None, 0)
    """
    clean_sub_name = clean_filename(subtitle_fname)
    if "." in clean_sub_name:
        clean_sub_name = clean_sub_name.rsplit(".", 1)[0]
    
    best_score = 0.0
    best_imdb = None
    best_season = None
    best_episode = None

    for i in range(1, db.current_db_index + 1):
        storage = db.dbs.get(f"storage_{i}")
        if storage is None:
            continue

        async for movie in storage["movie"].find({}):
            for q in movie.get("telegram", []):
                try:
                    decoded = await db.decode_string(q["id"])
                    if int(decoded["chat_id"]) == channel:
                        video_name = q.get("name", "")
                        clean_video_name = clean_filename(video_name)
                        if "." in clean_video_name:
                            clean_video_name = clean_video_name.rsplit(".", 1)[0]
                        
                        similarity = difflib.SequenceMatcher(
                            None, 
                            clean_sub_name.lower(), 
                            clean_video_name.lower()
                        ).ratio()
                        
                        if similarity > best_score and similarity >= 0.6:
                            best_score = similarity
                            best_imdb = movie.get("imdb_id")
                except Exception:
                    continue

        async for tv in storage["tv"].find({}):
            for season in tv.get("seasons", []):
                for episode in season.get("episodes", []):
                    for q in episode.get("telegram", []):
                        try:
                            decoded = await db.decode_string(q["id"])
                            if int(decoded["chat_id"]) == channel:
                                video_name = q.get("name", "")
                                clean_video_name = clean_filename(video_name)
                                if "." in clean_video_name:
                                    clean_video_name = clean_video_name.rsplit(".", 1)[0]
                                
                                similarity = difflib.SequenceMatcher(
                                    None, 
                                    clean_sub_name.lower(), 
                                    clean_video_name.lower()
                                ).ratio()
                                
                                if similarity > best_score and similarity >= 0.6:
                                    best_score = similarity
                                    best_imdb = tv.get("imdb_id")
                                    best_season = season.get("season_number")
                                    best_episode = episode.get("episode_number")
                        except Exception:
                            continue

    if best_imdb:
        return best_imdb, best_season, best_episode, best_score
    return None, None, None, 0


# ─────────────────────────────────────────────────────────────
# Video processing queue
# ─────────────────────────────────────────────────────────────

async def process_file():
    while True:
        metadata_info, channel, msg_id, size, title = await file_queue.get()
        async with db_lock:
            updated_id = await db.insert_media(metadata_info, channel=channel, msg_id=msg_id, size=size, name=title)
            if updated_id:
                LOGGER.info(f"{metadata_info['media_type']} updated with ID: {updated_id}")
            else:
                LOGGER.info("Update failed due to validation errors.")
        file_queue.task_done()

for _ in range(1):
    create_task(process_file())


# ─────────────────────────────────────────────────────────────
# Video handler
# ─────────────────────────────────────────────────────────────

@Client.on_message(filters.channel & (filters.document | filters.video))
async def file_receive_handler(client: Client, message: Message):
    if str(message.chat.id) not in Telegram.AUTH_CHANNEL:
        await message.reply_text("> Channel is not in AUTH_CHANNEL")
        return

    try:
        if message.document and _is_subtitle(message.document):
            await _handle_subtitle(message)
            return

        if message.video or (message.document and message.document.mime_type.startswith("video/")):
            file = message.video or message.document
            title = message.caption or file.file_name
            msg_id = message.id
            size = get_readable_file_size(file.file_size)
            channel = str(message.chat.id).replace("-100", "")

            metadata_info = await metadata(clean_filename(title), int(channel), msg_id)
            if metadata_info is None:
                LOGGER.warning(f"Metadata failed for file: {title} (ID: {msg_id})")
                return

            title = remove_urls(title)
            if not title.endswith(('.mkv', '.mp4')):
                title += '.mkv'

            if Backend.USE_DEFAULT_ID:
                new_caption = (message.caption + "

" + Backend.USE_DEFAULT_ID) if message.caption else Backend.USE_DEFAULT_ID
                create_task(edit_message(
                    chat_id=message.chat.id,
                    msg_id=message.id,
                    new_caption=new_caption
                ))

            await file_queue.put((metadata_info, int(channel), msg_id, size, title))
        else:
            await message.reply_text("> Not supported")

    except FloodWait as e:
        LOGGER.info(f"Sleeping for {str(e.value)}s")
        await asleep(e.value)
        await message.reply_text(
            text=f"Got Floodwait of {str(e.value)}s",
            disable_web_page_preview=True,
            parse_mode=ParseMode.MARKDOWN
        )


# ─────────────────────────────────────────────────────────────
# Subtitle handler
# ─────────────────────────────────────────────────────────────

async def _handle_subtitle(message: Message):
    doc = message.document
    caption = message.caption or ""
    fname = doc.file_name or "subtitle"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "srt"
    channel = str(message.chat.id).replace("-100", "")
    msg_id = message.id

    parsed = _parse_sub_caption(caption)

    if parsed:
        imdb_id, lang, season, episode = parsed
        detection_method = "caption"

    else:
        title = caption or fname
        clean_title = clean_filename(title)

        metadata_info = await metadata(clean_title, int(channel), msg_id)

        if metadata_info is None:
            await message.reply_text(
                "Could not auto-detect movie/show from filename.

"
                "Use a manual caption:
"
                "[SUB:tt1234567 en]
"
                "or for a TV episode:
"
                "[SUB:tt1234567 S01E02 en]",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        imdb_id = metadata_info.get("imdb_id")
        if not imdb_id:
            await message.reply_text(
                "Movie found on TMDB but has no IMDB ID.

"
                "Add the caption manually:
[SUB:tt1234567 en]",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        season = metadata_info.get("season") or None
        episode = metadata_info.get("episode") or None
        lang = _detect_language(fname)
        detection_method = "auto"

    subtitle_id = await encode_string({"chat_id": int(channel), "msg_id": msg_id})

    success = await db.insert_subtitle(
        imdb_id=imdb_id,
        subtitle_id=subtitle_id,
        language=lang,
        name=fname,
        fmt=ext,
        season_number=season,
        episode_number=episode,
    )

    if not success:
        LOGGER.info(f"IMDB ID {imdb_id} not found in DB, trying filename matching for subtitle: {fname}")
        matched_imdb, matched_season, matched_episode, similarity = await _find_matching_media_by_filename(
            fname, int(channel)
        )
        
        if matched_imdb:
            success = await db.insert_subtitle(
                imdb_id=matched_imdb,
                subtitle_id=subtitle_id,
                language=lang,
                name=fname,
                fmt=ext,
                season_number=matched_season or season,
                episode_number=matched_episode or episode,
            )
            if success:
                imdb_id = matched_imdb
                season = matched_season or season
                episode = matched_episode or episode
                detection_method = "filename_match"
                LOGGER.info(f"Subtitle filename matched: {matched_imdb} S{season}E{episode} [{lang}] -> msg {msg_id} (similarity: {similarity:.1%})")

    lang_label = LANG_NAMES.get(lang, lang.upper())
    ep_label = f" S{season:02d}E{episode:02d}" if season and episode else ""
    
    if detection_method == "caption":
        method_tag = "Caption"
    elif detection_method == "auto":
        method_tag = "Auto-detected"
    else:
        method_tag = "Filename matched"

    if success:
        LOGGER.info(f"Subtitle linked [{detection_method}]: {imdb_id}{ep_label} [{lang}] msg {msg_id}")
        await message.reply_text(
            f"Subtitle linked!

"
            f"IMDB: {imdb_id}{ep_label}
"
            f"Language: {lang_label}
"
            f"File: {fname}
"
            f"{method_tag}",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await message.reply_text(
            f"Could not find {imdb_id} in the database.
"
            "Make sure the movie/series video is indexed first, then re-send the subtitle.

"
            "Tried filename matching but no similar video was found.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ─────────────────────────────────────────────────────────────
# Edited message handler
# ─────────────────────────────────────────────────────────────

@Client.on_edited_message(filters.channel & (filters.document | filters.video))
async def file_edited_handler(client: Client, message: Message):
    if str(message.chat.id) not in Telegram.AUTH_CHANNEL:
        return
    try:
        if message.video or (message.document and message.document.mime_type.startswith("video/")):
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

                await file_queue.put((metadata_info, int(channel), msg_id, size, title))
    except Exception as e:
        LOGGER.error(f"Error handling edited generic file {message.id}: {e}")


# ─────────────────────────────────────────────────────────────
# Deleted message handler
# ─────────────────────────────────────────────────────────────

@Client.on_deleted_messages(filters.channel)
async def file_deleted_handler(client: Client, messages: list[Message]):
    try:
        for message in messages:
            if message.chat and str(message.chat.id) in Telegram.AUTH_CHANNEL:
                channel = str(message.chat.id).replace("-100", "")
                msg_id = message.id
                try:
                    stream_id_hash = await encode_string({"chat_id": int(channel), "msg_id": msg_id})
                    deleted = await db.delete_media_by_stream_id(stream_id_hash)
                    if deleted:
                        LOGGER.info(f"Automatically purged deleted message {msg_id} from database.")
                except Exception as ex:
                    LOGGER.error(f"Failed to scrub deleted message {msg_id}: {ex}")
    except Exception as e:
        LOGGER.error(f"Error handling deleted messages: {e}")
