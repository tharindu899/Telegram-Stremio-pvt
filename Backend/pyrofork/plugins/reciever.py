from asyncio import create_task, sleep as asleep, Queue, Lock
import re
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

# Subtitle file extensions
SUBTITLE_EXTENSIONS = [".srt", ".ass", ".ssa", ".sub", ".vtt", ".idx", ".sup"]


def extract_language_from_filename(filename: str):
    """Extract language code from subtitle filename"""
    patterns = [
        r'.([a-z]{2,3}).',  # file.en.srt
        r'[([a-z]{2,3})]',  # file [en].srt
        r'(([a-z]{2,3}))',  # file (en).srt
    ]
    for pattern in patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            lang = match.group(1).lower()
            if len(lang) <= 3:
                return lang
    return None


def extract_format_from_filename(filename: str):
    """Extract subtitle format from filename"""
    for ext in SUBTITLE_EXTENSIONS:
        if filename.lower().endswith(ext):
            return ext.lstrip('.')
    return None


async def find_associated_video(client, chat_id, subtitle_msg_id, lookback=10):
    """Find the most recent video message before this subtitle"""
    try:
        messages = await client.get_messages(
            chat_id, 
            range(subtitle_msg_id - lookback, subtitle_msg_id)
        )
        for msg in messages:
            if msg.video or (msg.document and 
                getattr(msg.document, "mime_type", "").startswith("video/")):
                return msg.id
    except Exception as e:
        LOGGER.error(f"Error finding associated video: {e}")
    return None


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


# NEW: Handler for subtitle files
@Client.on_message(filters.channel & filters.document)
async def subtitle_receive_handler(client: Client, message: Message):
    """Handle incoming subtitle files"""
    if str(message.chat.id) not in Telegram.AUTH_CHANNEL:
        return
    
    if not message.document:
        return
    
    file = message.document
    filename = file.file_name or ""
    
    # Check if it's a subtitle file
    is_subtitle = any(filename.lower().endswith(ext) for ext in SUBTITLE_EXTENSIONS)
    
    if is_subtitle:
        try:
            msg_id = message.id
            channel = str(message.chat.id).replace("-100", "")
            size = get_readable_file_size(file.file_size)
            
            # Extract metadata
            language = extract_language_from_filename(filename)
            subtitle_format = extract_format_from_filename(filename)
            
            # Find associated video
            video_msg_id = await find_associated_video(
                client, message.chat.id, msg_id
            )
            
            if video_msg_id:
                # Get the video's encoded string
                video_stream_id = await encode_string({
                    "chat_id": int(channel),
                    "msg_id": video_msg_id
                })
                
                # Encode the subtitle message info
                encoded_string = await encode_string({
                    "chat_id": int(channel),
                    "msg_id": msg_id
                })
                
                # Create subtitle info
                subtitle_info = {
                    'encoded_string': encoded_string,
                    'channel': int(channel),
                    'msg_id': msg_id,
                    'name': filename,
                    'size': size,
                    'language': language or "unknown",
                    'format': subtitle_format or "srt",
                    'video_msg_id': video_msg_id,
                    'video_encoded': video_stream_id
                }
                
                # Store in database
                await db.insert_subtitle(subtitle_info)
                LOGGER.info(f"Subtitle processed: {filename} for video {video_msg_id}")
            else:
                LOGGER.warning(f"No associated video found for subtitle: {filename}")
                
        except Exception as e:
            LOGGER.error(f"Error processing subtitle {filename}: {e}")


# UPDATED: Video file handler - skip subtitle files
@Client.on_message(filters.channel & (filters.document | filters.video))
async def file_receive_handler(client: Client, message: Message):
    if str(message.chat.id) in Telegram.AUTH_CHANNEL:
        try:
            # Skip if it's a subtitle file (handled by subtitle_receive_handler)
            if message.document:
                filename = message.document.file_name or ""
                is_subtitle = any(filename.lower().endswith(ext) for ext in SUBTITLE_EXTENSIONS)
                if is_subtitle:
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
    else:
        await message.reply_text("> Channel is not in AUTH_CHANNEL")
        

# NEW: Handler for edited subtitle files
@Client.on_edited_message(filters.channel & filters.document)
async def subtitle_edited_handler(client: Client, message: Message):
    """Handle edited subtitle files"""
    if str(message.chat.id) in Telegram.AUTH_CHANNEL:
        if message.document:
            file = message.document
            filename = file.file_name or ""
            
            is_subtitle = any(filename.lower().endswith(ext) for ext in SUBTITLE_EXTENSIONS)
            
            if is_subtitle:
                try:
                    msg_id = message.id
                    channel = str(message.chat.id).replace("-100", "")
                    size = get_readable_file_size(file.file_size)
                    
                    # Delete old subtitle entry
                    stream_id_hash = await encode_string({
                        "chat_id": int(channel), 
                        "msg_id": msg_id
                    })
                    await db.delete_subtitles_by_stream_id(stream_id_hash)
                    
                    # Re-process with new info
                    language = extract_language_from_filename(filename)
                    subtitle_format = extract_format_from_filename(filename)
                    video_msg_id = await find_associated_video(
                        client, message.chat.id, msg_id
                    )
                    
                    if video_msg_id:
                        video_stream_id = await encode_string({
                            "chat_id": int(channel),
                            "msg_id": video_msg_id
                        })
                        encoded_string = await encode_string({
                            "chat_id": int(channel),
                            "msg_id": msg_id
                        })
                        
                        subtitle_info = {
                            'encoded_string': encoded_string,
                            'channel': int(channel),
                            'msg_id': msg_id,
                            'name': filename,
                            'size': size,
                            'language': language or "unknown",
                            'format': subtitle_format or "srt",
                            'video_msg_id': video_msg_id,
                            'video_encoded': video_stream_id
                        }
                        
                        await db.insert_subtitle(subtitle_info)
                        LOGGER.info(f"Updated subtitle: {filename}")
                        
                except Exception as e:
                    LOGGER.error(f"Error updating subtitle: {e}")


# UPDATED: Delete handler to also remove subtitles
@Client.on_deleted_messages(filters.channel)
async def file_deleted_handler(client: Client, messages: list[Message]):
    try:
        for message in messages:
            if message.chat and str(message.chat.id) in Telegram.AUTH_CHANNEL:
                channel = str(message.chat.id).replace("-100", "")
                msg_id = message.id
                
                try:
                    stream_id_hash = await encode_string({
                        "chat_id": int(channel), 
                        "msg_id": msg_id
                    })
                    
                    # Delete media
                    deleted = await db.delete_media_by_stream_id(stream_id_hash)
                    
                    # Delete subtitles
                    deleted_subs = await db.delete_subtitles_by_stream_id(stream_id_hash)
                    
                    if deleted or deleted_subs:
                        LOGGER.info(f"Automatically purged deleted message {msg_id} from database.")
                        
                except Exception as ex:
                    LOGGER.error(f"Failed to scrub deleted message {msg_id}: {ex}")
                    
    except Exception as e:
        LOGGER.error(f"Error handling deleted messages: {e}")
