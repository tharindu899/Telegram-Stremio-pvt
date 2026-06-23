
"""Extra owner commands for the full feature pack.

Commands:
    /status                 — quick running/bot status
    /find <title>           — alias for DB title search
    /speedtest <chat> <msg> — test a Telegram file message across bot clients
    /linktoken <token> <user_id> — link an access token to a Telegram user
"""

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from Backend import db, StartTime, __version__
from Backend.config import Telegram
from Backend.helper.custom_filter import CustomFilters
from Backend.pyrofork.bot import StreamBot, multi_clients
from Backend.helper.custom_dl import run_speed_test
from Backend.helper.pyro import get_readable_time
from time import time
import re




@Client.on_message(filters.command("tools") & filters.private & CustomFilters.owner, group=10)
async def tools_command(client: Client, message: Message):
    base = (Telegram.BASE_URL or "").rstrip('/')
    if not base:
        await message.reply_text(
            "🧰 Admin Tools URL is not available because BASE_URL is empty. Set BASE_URL in your Space variables.",
            quote=True,
        )
        return
    url = f"{base}/admin/tools"
    await message.reply_text(
        "<blockquote>🧰 <b>Admin Tools</b></blockquote>\n\n"
        "Use the web control center for scan, rescan, deadcheck, dedupe, cache, analytics, bot speed, and storage charts.\n\n"
        f"<code>{url}</code>",
        quote=True,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧰 Open Admin Tools", url=url)]]),
    )


@Client.on_message(filters.command("status") & filters.private & CustomFilters.owner, group=10)
async def owner_status(client: Client, message: Message):
    try:
        db_stats = await db.get_database_stats()
        movies = sum(x.get("movie_count", 0) for x in db_stats)
        series = sum(x.get("tv_count", 0) for x in db_stats)
    except Exception:
        movies = series = 0
    text = (
        f"<blockquote>✅ <b>Telegram-Stremio is running</b></blockquote>\n\n"
        f"Version: <code>{__version__}</code>\n"
        f"Uptime: <code>{get_readable_time(time() - StartTime)}</code>\n"
        f"Main bot: <code>@{getattr(StreamBot, 'username', 'unknown')}</code>\n"
        f"Connected clients: <code>{len(multi_clients)}</code>\n"
        f"Movies: <code>{movies}</code> | Series: <code>{series}</code>\n"
        f"Sources: <code>{len(Telegram.AUTH_CHANNEL)}</code>"
    )
    await message.reply_text(text, quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("find") & filters.private & CustomFilters.owner, group=10)
async def find_command(client: Client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("Usage: <code>/find movie or series name</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        return
    query = args[1].strip()
    regex = {"title": {"$regex": re.escape(query), "$options": "i"}}
    rows = []
    for i in range(1, db.current_db_index + 1):
        storage = db.dbs.get(f"storage_{i}")
        if storage is None: continue
        for media_type, icon, col in [("movie", "🎬", "movie"), ("tv", "📺", "tv")]:
            docs = await storage[col].find(regex, {"title":1,"release_year":1,"imdb_id":1,"tmdb_id":1,"telegram":1,"seasons":1}).limit(8).to_list(None)
            for d in docs:
                streams = len(d.get("telegram") or []) if media_type == "movie" else sum(len(ep.get("telegram") or []) for s in d.get("seasons") or [] for ep in s.get("episodes") or [])
                rows.append(f"{icon} <b>{d.get('title','?')}</b> ({d.get('release_year','?')})\nIMDb: <code>{d.get('imdb_id','')}</code> · TMDB: <code>{d.get('tmdb_id','')}</code> · Streams: <code>{streams}</code>")
    await message.reply_text(("<blockquote>🔎 <b>Find results</b></blockquote>\n\n" + "\n\n".join(rows[:15])) if rows else f"No results for <b>{query}</b>", quote=True, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("speedtest") & filters.private & CustomFilters.owner, group=10)
async def speedtest_command(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.reply_text(
            "Usage: <code>/speedtest -100CHAT_ID MESSAGE_ID</code>\n\n"
            "Example: <code>/speedtest -1001234567890 55</code>",
            quote=True, parse_mode=enums.ParseMode.HTML,
        )
        return
    try:
        chat_id = int(args[1])
        msg_id = int(args[2])
    except Exception:
        await message.reply_text("Invalid chat/message id.", quote=True)
        return
    status = await message.reply_text("⚡ Running speed test…", quote=True)
    try:
        results = await run_speed_test(chat_id, msg_id)
        lines = []
        for r in results:
            lines.append(f"Bot {r.get('client_index')}: <code>{r.get('speed_mbps', r.get('avg_mbps', 0))}</code> MB/s · {r.get('status','ok')}")
        await status.edit_text("<blockquote>⚡ <b>Speed test result</b></blockquote>\n\n" + ("\n".join(lines) or "No result"), parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status.edit_text(f"❌ Speed test failed: <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("linktoken") & filters.private & CustomFilters.owner, group=10)
async def linktoken_command(client: Client, message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.reply_text("Usage: <code>/linktoken TOKEN USER_ID</code>", quote=True, parse_mode=enums.ParseMode.HTML)
        return
    token, uid = args[1], args[2]
    try:
        uid = int(uid)
    except Exception:
        await message.reply_text("USER_ID must be numeric Telegram ID.", quote=True)
        return
    ok = await db.link_token_user(token, uid)
    await message.reply_text((f"✅ Token linked to <code>{uid}</code>." if ok else "❌ Token not found or already linked."), quote=True, parse_mode=enums.ParseMode.HTML)
