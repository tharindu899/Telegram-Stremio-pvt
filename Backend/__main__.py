from asyncio import get_event_loop, sleep as asleep
import asyncio
import logging
from traceback import format_exc
from pyrogram import idle
from Backend import __version__, db
from Backend.helper.pinger import ping
from Backend.logger import LOGGER
from Backend.config import Telegram
from Backend.fastapi import server
from Backend.helper.pyro import restart_notification, setup_bot_commands
from Backend.pyrofork.bot import Helper, StreamBot
from Backend.pyrofork.clients import initialize_clients
from Backend.pyrofork.plugins.channels import _load_channels_from_db
from Backend.helper.subscription_checker import subscription_checker_loop
from Backend.helper.link_checker import DeadLinkChecker
from Backend.fastapi.main import app
from Backend.helper.auto_catalog import (
    start_auto_catalog_sync_background, start_auto_catalog_interval_loop,
    AUTO_SYNC_DELAY_SECONDS, AUTO_CATALOG_ON_STARTUP,
    AUTO_CATALOG_FULL_REBUILD_ON_STARTUP
)


loop = get_event_loop()

async def start_services():
    try:
        LOGGER.info(f"Initializing Telegram-Stremio v-{__version__}")

        # Start the FastAPI web server first. This makes Hugging Face Spaces show
        # the site even while Telegram/MongoDB secrets are being configured.
        LOGGER.info(f"Starting web server on 0.0.0.0:{Telegram.PORT} ...")
        loop.create_task(server.serve())
        await asleep(1.0)

        missing = []
        if not Telegram.API_ID:
            missing.append("API_ID")
        if not Telegram.API_HASH:
            missing.append("API_HASH")
        if not Telegram.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if not Telegram.HELPER_BOT_TOKEN:
            missing.append("HELPER_BOT_TOKEN")
        if len(Telegram.DATABASE) < 2:
            missing.append("DATABASE (tracking URI,storage URI)")

        if missing:
            LOGGER.warning(
                "Running in web-only setup mode. Add these Hugging Face Space secrets/variables for full streaming: "
                + ", ".join(missing)
            )
            await asyncio.Event().wait()
            return

        await db.connect()
        try:
            from Backend.helper.runtime_config import apply_runtime_config
            saved_runtime_config = await db.get_runtime_config_values()
            apply_runtime_config(saved_runtime_config, source="database")
        except Exception as e:
            LOGGER.error(f"Failed to load WebUI runtime config: {e}")
        await asleep(1.2)
        if getattr(db, "disabled", False):
            LOGGER.warning("Database is disabled; keeping web server alive in setup mode.")
            await asyncio.Event().wait()
            return

        # Load WebUI/source-manager channel entries before live intake begins.
        # This keeps the update filter and the scanner on the same source list.
        await _load_channels_from_db()

        # Import inside the running event loop: the receiver starts its queue
        # worker at module import time.
        from Backend.pyrofork.plugins.reciever import bind_live_receiver, verify_live_source_access

        # Bind live uploads explicitly before StreamBot starts. This avoids a
        # silent dependency on plugin auto-discovery for real-time channel posts.
        await bind_live_receiver(StreamBot)
        await StreamBot.start()
        StreamBot.username = StreamBot.me.username
        LOGGER.info(f"Bot Client : [@{StreamBot.username}]")
        await verify_live_source_access(StreamBot)
        await asleep(1.2)

        await Helper.start()
        Helper.username = Helper.me.username
        LOGGER.info(f"Helper Bot Client : [@{Helper.username}]")
        await asleep(1.2)

        LOGGER.info("Initializing Multi Clients...")
        await initialize_clients()
        await asleep(2)

        await setup_bot_commands(StreamBot)
        await asleep(2)

        LOGGER.info('Initializing Telegram-Stremio background tasks...')
        await restart_notification()
        loop.create_task(ping())

        link_checker_task = DeadLinkChecker(db, app, check_interval_hours=24)
        loop.create_task(link_checker_task.start())

        if AUTO_CATALOG_ON_STARTUP:
            loop.create_task(start_auto_catalog_sync_background(
                db,
                delay_seconds=AUTO_SYNC_DELAY_SECONDS,
                full_rebuild=AUTO_CATALOG_FULL_REBUILD_ON_STARTUP,
            ))

        loop.create_task(start_auto_catalog_interval_loop(db))

        if Telegram.SUBSCRIPTION:
            loop.create_task(subscription_checker_loop(StreamBot))
            LOGGER.info("Subscription Checker Task Started.")

        LOGGER.info("Telegram-Stremio Started Successfully!")
        await idle()
    except Exception:
        LOGGER.error("Error during startup:\n" + format_exc())
        # Keep the container alive long enough for the web server logs/health page
        # to be visible instead of instantly crash-looping.
        await asyncio.Event().wait()

async def stop_services():
    try:
        LOGGER.info("Stopping services...")

        pending_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in pending_tasks:
            task.cancel()
        
        await asyncio.gather(*pending_tasks, return_exceptions=True)

        for client in (StreamBot, Helper):
            try:
                if getattr(client, "is_connected", False):
                    await client.stop()
            except Exception:
                LOGGER.error("Error while stopping Telegram client:\n" + format_exc())

        await db.disconnect()
        
        LOGGER.info("Services stopped successfully.")
    except Exception:
        LOGGER.error("Error during shutdown:\n" + format_exc())

if __name__ == '__main__':
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        LOGGER.info('Service Stopping...')
    except Exception:
        LOGGER.error(format_exc())
    finally:
        loop.run_until_complete(stop_services())
        loop.stop()
        logging.shutdown()  
