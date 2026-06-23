"""Owner-only manual dead-link checker commands.

Commands:
    /deadcheck  — Run Telegram dead-link check now
    /dlcheck    — Alias for /deadcheck
    /dncheck    — Typo-friendly alias
"""

import asyncio
import time

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Backend import db
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.link_checker import DeadLinkChecker
from Backend.logger import LOGGER

_deadcheck_running = False


@Client.on_message(filters.command(["deadcheck", "dlcheck", "dncheck"]) & filters.private & CustomFilters.owner, group=10)
async def deadcheck_command(client: Client, message: Message):
    """Run the dead-link checker immediately from Telegram."""
    global _deadcheck_running
    if _deadcheck_running:
        await message.reply_text(
            "⚠️ Dead-link check is already running. Please wait for it to finish.",
            quote=True,
        )
        return

    _deadcheck_running = True
    started = time.time()
    status_msg = await message.reply_text(
        "🔎 <b>Dead-link check started…</b>\n\n"
        "Checking movie, series, split ZIP, and split video entries.",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        checker = DeadLinkChecker(db, client)
        result = await checker._scan_all_media()
        elapsed = int(time.time() - started)
        scanned = int(result.get("scanned", 0))
        flagged = int(result.get("flagged", 0))
        errors = int(result.get("errors", 0))
        skipped = int(result.get("skipped", 0))
        repaired = int(result.get("repaired", 0))
        still_dead = int(result.get("still_dead", 0))

        text = (
            "✅ <b>Dead-link check complete</b>\n\n"
            f"⏱ Time: <code>{elapsed}s</code>\n"
            f"📦 Checked active: <code>{scanned}</code>\n"
            f"🛠 Repaired old dead flags: <code>{repaired}</code>\n"
            f"☠️ Still dead: <code>{still_dead}</code>\n"
            f"🚩 Newly flagged dead: <code>{flagged}</code>\n"
            f"⏭ Skipped: <code>{skipped}</code>\n"
            f"❌ Errors: <code>{errors}</code>"
        )
        await status_msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        LOGGER.error("Manual dead-link check failed: %s", e)
        await status_msg.edit_text(
            f"❌ Dead-link check failed: <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    finally:
        _deadcheck_running = False
