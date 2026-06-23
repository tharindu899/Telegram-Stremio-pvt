from asyncio import sleep
from time import monotonic

from pyrogram.errors import FloodWait

from Backend.logger import LOGGER
from Backend.pyrofork.bot import Helper

# A missing delete permission used to create one 403 + one warning for every
# replacement/duplicate. Pause that chat briefly after the first failure.
DELETE_PERMISSION_COOLDOWN_SECONDS = 15 * 60
_delete_blocked_until: dict[str, float] = {}


async def edit_message(chat_id: int, msg_id: int, new_caption: str):
    try:
        await Helper.edit_message_caption(
            chat_id=chat_id,
            message_id=msg_id,
            caption=new_caption
        )
        await sleep(2)
    except FloodWait as e:
        LOGGER.warning(f"FloodWait for {e.value} seconds while editing message {msg_id} in {chat_id}")
        await sleep(e.value)
    except Exception as e:
        LOGGER.error(f"Error while editing message {msg_id} in {chat_id}: {e}")


async def delete_message(chat_id: int, msg_id: int):
    chat_key = str(chat_id)
    now = monotonic()
    if _delete_blocked_until.get(chat_key, 0.0) > now:
        return

    try:
        await Helper.delete_messages(
            chat_id=chat_id,
            message_ids=msg_id
        )
        await sleep(2)
        LOGGER.debug(f"Deleted Telegram message {msg_id} in {chat_id}")
    except FloodWait as e:
        LOGGER.warning(f"FloodWait for {e.value} seconds while deleting message {msg_id} in {chat_id}")
        await sleep(e.value)
    except Exception as e:
        err = str(e)
        if "MESSAGE_DELETE_FORBIDDEN" in err or "don't have rights to delete" in err or "not the author" in err:
            _delete_blocked_until[chat_key] = monotonic() + DELETE_PERMISSION_COOLDOWN_SECONDS
            LOGGER.warning(
                f"Telegram delete permission is unavailable in {chat_id}; duplicate cleanup continues in MongoDB. "
                f"Further delete attempts for this chat are paused for {DELETE_PERMISSION_COOLDOWN_SECONDS // 60} minutes."
            )
            return
        LOGGER.error(f"Error while deleting message {msg_id} in {chat_id}: {e}")
