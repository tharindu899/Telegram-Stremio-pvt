from pyrogram import filters, Client, enums
from pyrogram.types import Message
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from asyncio import create_subprocess_exec, gather
from aiofiles import open as aiopen
from os import execl as osexecl
import shutil

@Client.on_message(filters.command('restart') & filters.private & CustomFilters.owner, group=10)
async def restart(client: Client, message: Message):
    try:
        restart_message = await message.reply_text(
            '<blockquote>⚙️ Restarting Backend API... \n\n✨ Please wait as we bring everything back online! 🚀</blockquote>',
            quote=True,
            parse_mode=enums.ParseMode.HTML
        )

        proc1 = await create_subprocess_exec('uv', 'run', 'update.py')
        await gather(proc1.wait())

        async with aiopen(".restartmsg", "w") as f:
            await f.write(f"{restart_message.chat.id}\n{restart_message.id}\n")

        LOGGER.info("Restarting the bot using uv package manager...")

        uv_path = shutil.which("uv")
        if uv_path:
            osexecl(uv_path, uv_path, "run", "-m", "Backend")
        else:
            raise RuntimeError("uv not found in PATH.")

    except Exception as e:
        LOGGER.error(f"Error during restart: {e}")
        await message.reply_text("**❌ Failed to restart. Check logs for details.**")
        
