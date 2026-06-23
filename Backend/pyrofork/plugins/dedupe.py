"""Duplicate cleanup commands.

Commands (owner-only private chat):
    /dedupe          — remove exact duplicate streams/subtitles and delete old Telegram messages
    /removedupe      — alias
    /rmdup           — alias
"""

from pyrogram import Client, filters, enums

from Backend import db
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER


@Client.on_message(filters.command(["dedupe", "removedupe", "rmdup"]) & filters.private & CustomFilters.owner, group=10)
async def dedupe_command(client: Client, message):
    status = await message.reply_text(
        "🧹 <b>Duplicate cleanup started…</b>\n"
        "Old duplicate Telegram messages will be deleted after DB cleanup.",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        stats = await db.remove_duplicate_entries(delete_old_messages=True)
        await status.edit_text(
            "✅ <b>Duplicate cleanup complete</b>\n\n"
            f"🎬 Movie duplicate streams removed: <code>{stats.get('movies', 0)}</code>\n"
            f"📺 Episode duplicate streams removed: <code>{stats.get('episodes', 0)}</code>\n"
            f"💬 Duplicate subtitles removed: <code>{stats.get('subtitles', 0)}</code>\n"
            f"🗑 Old Telegram deletes queued: <code>{stats.get('old_messages_queued', 0)}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        LOGGER.exception("Duplicate cleanup failed: %s", e)
        await status.edit_text(f"❌ Duplicate cleanup failed:\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
