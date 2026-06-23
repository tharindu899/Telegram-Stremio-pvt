from pyrogram import Client, filters, enums
from pyrogram.types import Message
from Backend.config import Telegram


@Client.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    if Telegram.SUBSCRIPTION:
        text = (
            "<blockquote>🤖 <b>TG Stremio Commands</b></blockquote>\n\n"
            "🎬 <code>/start</code> — Addon link / membership menu\n"
            "🩺 <code>/status</code> — Subscription expiry + runtime status\n"
            "🧰 <code>/tools</code> — Open Admin Tools panel\n"
            "📖 <code>/help</code> — Show this help\n\n"
            "🧰 Scan, deadcheck, dedupe, cache, analytics and speed tools are handled from the Admin Tools page."
        )
    else:
        text = (
            "<blockquote>🤖 <b>TG Stremio Commands</b></blockquote>\n\n"
            "🎬 <code>/start</code> — Get your Stremio addon URL\n"
            "🩺 <code>/status</code> — Runtime + library status\n"
            "🧰 <code>/tools</code> — Open Admin Tools panel\n"
            "📖 <code>/help</code> — Show this help\n\n"
            "🧰 Owner admin actions are handled from the Admin Tools page."
        )
    await message.reply_text(text, quote=True, parse_mode=enums.ParseMode.HTML)
