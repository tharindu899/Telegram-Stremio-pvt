import asyncio
from datetime import datetime
from Backend.logger import LOGGER
from Backend.helper.encrypt import decode_string

class DeadLinkChecker:
    def __init__(self, db, app, check_interval_hours: int = 24):
        self.db = db
        self.app = app
        self.check_interval_seconds = check_interval_hours * 3600
        self.is_running = False

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        LOGGER.info(f"Started Dead Link Checker background task (Interval: {self.check_interval_seconds}s)")
        asyncio.create_task(self._run_loop())

    async def _run_loop(self):
        # Wait a minute before starting the first scan so the bots can boot up
        await asyncio.sleep(60)
        
        while self.is_running:
            try:
                LOGGER.info("Starting Dead Link Checker scan...")
                await self._scan_all_media()
                LOGGER.info("Dead Link Checker scan complete.")
            except Exception as e:
                LOGGER.error(f"Error in Dead Link Checker loop: {e}")
            
            # Sleep until the next interval
            await asyncio.sleep(self.check_interval_seconds)

    async def _scan_all_media(self):
        # We need at least one bot client to check messages
        from Backend.pyrofork.bot import multi_clients
        if not multi_clients:
            LOGGER.warning("No bot clients available for Dead Link Checker.")
            return

        # Use the primary client to fetch messages
        client = multi_clients.get(0) or next(iter(multi_clients.values()))

        # Iterate through all active storage DBs
        for i in range(1, self.db.current_db_index + 1):
            db_key = f"storage_{i}"
            active_db = self.db.dbs[db_key]

            # 1. Scan Movies
            try:
                # Find movies that have telegram links and are NOT already marked dead
                movie_cursor = active_db["movie"].find({
                    "telegram": {"$exists": True, "$not": {"$size": 0}},
                    "telegram.is_dead": {"$ne": True}
                })
                async for movie in movie_cursor:
                    tmdb_id = movie.get("tmdb_id")
                    for quality in movie.get("telegram", []):
                        if not quality.get("is_dead"):
                            is_alive = await self._check_file_alive(client, quality.get("id"))
                            if not is_alive:
                                LOGGER.warning(f"Found dead link for Movie {tmdb_id} (Quality: {quality.get('quality')})")
                                await self.db.flag_dead_link("movie", tmdb_id, i, quality.get("id"))
                            # Add a tiny sleep to avoid flooding Telegram API during scan
                            await asyncio.sleep(0.5)
            except Exception as e:
                LOGGER.error(f"Error scanning movies in DB {i}: {e}")

            # 2. Scan TV Shows
            try:
                tv_cursor = active_db["tv"].find({
                    "seasons.episodes.telegram": {"$exists": True, "$not": {"$size": 0}},
                    "seasons.episodes.telegram.is_dead": {"$ne": True}
                })
                async for tv in tv_cursor:
                    tmdb_id = tv.get("tmdb_id")
                    for season in tv.get("seasons", []):
                        for ep in season.get("episodes", []):
                            for quality in ep.get("telegram", []):
                                if not quality.get("is_dead"):
                                    is_alive = await self._check_file_alive(client, quality.get("id"))
                                    if not is_alive:
                                        LOGGER.warning(f"Found dead link for TV {tmdb_id} S{season.get('season_number')}E{ep.get('episode_number')} (Quality: {quality.get('quality')})")
                                        await self.db.flag_dead_link("tv", tmdb_id, i, quality.get("id"))
                                    await asyncio.sleep(0.5)
            except Exception as e:
                LOGGER.error(f"Error scanning TV shows in DB {i}: {e}")

    async def _check_file_alive(self, client, quality_id: str) -> bool:
        """
        Decodes the quality_id to a Telegram chat_id and message_id,
        then attempts to fetch the message to see if it (and its media) still exist.
        """
        try:
            decoded = await decode_string(quality_id)
            if not decoded or "chat_id" not in decoded or "msg_id" not in decoded:
                return False
                
            chat_id = int(f"-100{decoded['chat_id']}")
            msg_id = int(decoded["msg_id"])

            # Use get_messages to fetch exactly one message
            messages = await client.get_messages(chat_id, message_ids=[msg_id])
            if not messages or len(messages) == 0:
                return False
                
            msg = messages[0]
            if msg.empty or (msg.document is None and msg.video is None and msg.audio is None):
                return False

            return True

        except Exception as e:
            # If the channel is banned, chat_id is invalid, or any other critical error occurs
            LOGGER.error(f"Link checker failed to resolve {quality_id}: {e}")
            return False
