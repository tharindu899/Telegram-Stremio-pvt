from pyrogram import Client
from Backend.config import Telegram

StreamBot = Client(
    name='bot',
    api_id=Telegram.API_ID,
    api_hash=Telegram.API_HASH,
    bot_token=Telegram.BOT_TOKEN,
    plugins={"root": "Backend/pyrofork/plugins"},
    sleep_threshold=20,
    workers=6,
    max_concurrent_transmissions=10
)

Helper = Client(
    "helper",
    api_id=Telegram.API_ID,
    api_hash=Telegram.API_HASH,
    bot_token=Telegram.HELPER_BOT_TOKEN,
    sleep_threshold=20,
    workers=6,
    max_concurrent_transmissions=10
)

multi_clients = {}
work_loads = {}
client_dc_map = {}
client_failures = {}  
client_avg_mbps = {}
def is_subtitle_file(filename: str) -> bool:
    if not filename: return False
    filename_lower = filename.lower()
    for ext in SUBTITLE_EXTENSIONS:
        if filename_lower.endswith(ext): return True
    return False

def detect_subtitle_language(filename: str) -> tuple:
    if not filename: return ("en", "English")
    filename_lower = filename.lower()
    for code, name in LANGUAGE_MAP.items():
        if code in filename_lower or name.lower() in filename_lower:
            return (code, name)
    return ("en", "English")

def get_subtitle_type(filename: str) -> str:
    if not filename: return "srt"
    filename_lower = filename.lower()
    for ext, type_ in SUBTITLE_TYPES.items():
        if filename_lower.endswith(ext): return type_
    return "srt"

async def handle_subtitle_message(client, message):
    if message.document:
        if is_subtitle_file(message.document.file_name or ""):
            await handle_subtitle_message(client, message)
            return
    if message.document:
        if is_subtitle_file(message.document.file_name or ""):
            await handle_subtitle_message(client, message)
            return
    if message.document:
        file_name = message.document.file_name or ""
        if is_subtitle_file(file_name):
            await handle_subtitle_message(client, message)
            return
    try:
        file_name = message.document.file_name or ""
        file_id = message.document.file_id
        msg_id = message.id
        chat_id = message.chat.id
        language_code, language_name = detect_subtitle_language(file_name)
        subtitle_type = get_subtitle_type(file_name)
        file_size = message.document.file_size or 0
        size_str = f"{file_size / 1024:.1f}KB"
        subtitle_data = {
            "msg_id": msg_id, "chat_id": chat_id, "file_id": str(file_id),
            "name": file_name, "language": language_code, "language_name": language_name,
            "type": subtitle_type, "size": size_str, "file_size": file_size
        }
        imdb_id = None
        if message.caption:
            import re
            match = re.search(r'tt\d{7,}', message.caption)
            if match: imdb_id = match.group(0)
        if imdb_id:
            from Backend import db
            await db.add_subtitle(imdb_id, "movie", subtitle_data)
            LOGGER.info(f"Stored subtitle {file_name} for {imdb_id}")
        else:
            LOGGER.info(f"Subtitle {file_name} - Chat: {chat_id}, Msg: {msg_id}")
    except Exception as e:
        LOGGER.error(f"Error handling subtitle: {e}")

SUBTITLE_EXTENSIONS = ['.srt', '.ass', '.ssa', '.sub', '.vtt', '.txt', '.smi', '.rt']
LANGUAGE_MAP = {'en':'English','es':'Spanish','fr':'French','de':'German','it':'Italian','pt':'Portuguese','ru':'Russian','zh':'Chinese','ja':'Japanese','ar':'Arabic','hi':'Hindi','tr':'Turkish'}
SUBTITLE_TYPES = {'.srt':'srt','.ass':'ass','.ssa':'ssa','.sub':'sub','.vtt':'vtt','.txt':'txt','.smi':'smi','.rt':'rt'}

def is_subtitle_file(filename: str) -> bool:
    if not filename: return False
    for ext in SUBTITLE_EXTENSIONS:
        if filename.lower().endswith(ext): return True
    return False

def detect_subtitle_language(filename: str) -> tuple:
    if not filename: return ("en", "English")
    fl = filename.lower()
    for code, name in LANGUAGE_MAP.items():
        if code in fl or name.lower() in fl: return (code, name)
    return ("en", "English")

def get_subtitle_type(filename: str) -> str:
    if not filename: return "srt"
    fl = filename.lower()
    for ext, typ in SUBTITLE_TYPES.items():
        if fl.endswith(ext): return typ
    return "srt"

async def handle_subtitle_message(client, message):
    if message.document:
        if is_subtitle_file(message.document.file_name or ""):
            await handle_subtitle_message(client, message)
            return
    if message.document:
        if is_subtitle_file(message.document.file_name or ""):
            await handle_subtitle_message(client, message)
            return
    try:
        file_name = message.document.file_name or ""
        if not is_subtitle_file(file_name): return
        file_id = message.document.file_id
        msg_id = message.id
        chat_id = message.chat.id
        lang_code, lang_name = detect_subtitle_language(file_name)
        sub_type = get_subtitle_type(file_name)
        file_size = message.document.file_size or 0
        subtitle_data = {
            "msg_id": msg_id, "chat_id": chat_id, "file_id": str(file_id),
            "name": file_name, "language": lang_code, "language_name": lang_name,
            "type": sub_type, "size": f"{file_size/1024:.1f}KB", "file_size": file_size
        }
        imdb_id = None
        if message.caption:
            import re
            match = re.search(r'tt\d{7,}', message.caption)
            if match: imdb_id = match.group(0)
        if imdb_id:
            from Backend import db
            await db.add_subtitle(imdb_id, "movie", subtitle_data)
            LOGGER.info(f"Stored subtitle {file_name} for {imdb_id}")
        else:
            LOGGER.info(f"Subtitle {file_name} - Chat: {chat_id}, Msg: {msg_id}")
    except Exception as e:
        LOGGER.error(f"Error: {e}")

SUBTITLE_EXTENSIONS = ['.srt', '.ass', '.ssa', '.sub', '.vtt', '.txt', '.smi', '.rt']
LANGUAGE_MAP = {'en':'English','es':'Spanish','fr':'French','de':'German','it':'Italian','pt':'Portuguese','ru':'Russian','zh':'Chinese','ja':'Japanese','ar':'Arabic','hi':'Hindi','tr':'Turkish'}
SUBTITLE_TYPES = {'.srt':'srt','.ass':'ass','.ssa':'ssa','.sub':'sub','.vtt':'vtt','.txt':'txt','.smi':'smi','.rt':'rt'}

def is_subtitle_file(filename: str) -> bool:
    if not filename: return False
    for ext in SUBTITLE_EXTENSIONS:
        if filename.lower().endswith(ext): return True
    return False

def detect_subtitle_language(filename: str) -> tuple:
    if not filename: return ("en", "English")
    fl = filename.lower()
    for code, name in LANGUAGE_MAP.items():
        if code in fl or name.lower() in fl: return (code, name)
    return ("en", "English")

def get_subtitle_type(filename: str) -> str:
    if not filename: return "srt"
    fl = filename.lower()
    for ext, typ in SUBTITLE_TYPES.items():
        if fl.endswith(ext): return typ
    return "srt"

async def handle_subtitle_message(client, message):
    if message.document:
        if is_subtitle_file(message.document.file_name or ""):
            await handle_subtitle_message(client, message)
            return
    try:
        file_name = message.document.file_name or ""
        if not is_subtitle_file(file_name): return
        file_id = message.document.file_id
        msg_id = message.id
        chat_id = message.chat.id
        lang_code, lang_name = detect_subtitle_language(file_name)
        sub_type = get_subtitle_type(file_name)
        file_size = message.document.file_size or 0
        subtitle_data = {"msg_id": msg_id, "chat_id": chat_id, "file_id": str(file_id), "name": file_name, "language": lang_code, "language_name": lang_name, "type": sub_type, "size": f"{file_size/1024:.1f}KB", "file_size": file_size}
        imdb_id = None
        if message.caption:
            import re
            match = re.search(r'tt\d{7,}', message.caption)
            if match: imdb_id = match.group(0)
        if imdb_id:
            from Backend import db
            await db.add_subtitle(imdb_id, "movie", subtitle_data)
            LOGGER.info(f"Stored subtitle {file_name} for {imdb_id}")
        else:
            LOGGER.info(f"Subtitle {file_name} - Chat: {chat_id}, Msg: {msg_id}")
    except Exception as e:
        LOGGER.error(f"Error: {e}")
