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
        result = {"scanned": 0, "flagged": 0, "skipped": 0, "errors": 0, "repaired": 0, "still_dead": 0}
        if not multi_clients:
            LOGGER.warning("No bot clients available for Dead Link Checker.")
            return result

        # Use the primary client to fetch messages
        client = multi_clients.get(0) or next(iter(multi_clients.values()))

        # First re-check entries that were already flagged dead. This is important
        # for split ZIP / split video streams: older builds could mark a split
        # entry dead before all parts were indexed or after a temporary Telegram
        # access error. If every part is alive now, remove the dead flag.
        try:
            repair_stats = await self.repair_dead_links(client)
            result["repaired"] += int(repair_stats.get("repaired", 0))
            result["still_dead"] += int(repair_stats.get("still_dead", 0))
            result["errors"] += int(repair_stats.get("errors", 0))
        except Exception as repair_error:
            result["errors"] += 1
            LOGGER.error("Dead-link repair pass failed: %s", repair_error)

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
                            result["scanned"] += 1
                            is_alive = await self._check_file_alive(client, quality.get("id"))
                            if not is_alive:
                                LOGGER.warning(f"Found dead link for Movie {tmdb_id} (Quality: {quality.get('quality')})")
                                await self.db.flag_dead_link("movie", tmdb_id, i, quality.get("id"))
                                result["flagged"] += 1
                            # Add a tiny sleep to avoid flooding Telegram API during scan
                            await asyncio.sleep(0.5)
                        else:
                            result["skipped"] += 1
            except Exception as e:
                result["errors"] += 1
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
                                    result["scanned"] += 1
                                    is_alive = await self._check_file_alive(client, quality.get("id"))
                                    if not is_alive:
                                        LOGGER.warning(f"Found dead link for TV {tmdb_id} S{season.get('season_number')}E{ep.get('episode_number')} (Quality: {quality.get('quality')})")
                                        await self.db.flag_dead_link("tv", tmdb_id, i, quality.get("id"))
                                        result["flagged"] += 1
                                    await asyncio.sleep(0.5)
                                else:
                                    result["skipped"] += 1
            except Exception as e:
                result["errors"] += 1
                LOGGER.error(f"Error scanning TV shows in DB {i}: {e}")

        return result


    async def repair_dead_links(self, client) -> dict:
        """Re-check already flagged dead entries and unflag them if Telegram is alive.

        Dead entries are normally hidden from Stremio, but split-file projects may
        have entries that were marked dead by an older build even though all parts
        still exist. This pass is safe: it only clears is_dead when the same
        encoded stream id can be resolved and all required messages/media exist.
        """
        result = {"checked_dead": 0, "repaired": 0, "still_dead": 0, "errors": 0}

        for i in range(1, self.db.current_db_index + 1):
            db_key = f"storage_{i}"
            active_db = self.db.dbs[db_key]

            try:
                movie_cursor = active_db["movie"].find({"telegram.is_dead": True})
                async for movie in movie_cursor:
                    changed = False
                    for quality in movie.get("telegram", []):
                        if not quality.get("is_dead"):
                            continue
                        result["checked_dead"] += 1
                        alive = await self._check_file_alive(client, quality.get("id"))
                        if alive:
                            quality.pop("is_dead", None)
                            changed = True
                            result["repaired"] += 1
                            LOGGER.info(
                                "Repaired dead flag for movie %s quality=%s name=%s",
                                movie.get("tmdb_id"), quality.get("quality"), quality.get("name")
                            )
                        else:
                            result["still_dead"] += 1
                        await asyncio.sleep(0.25)
                    if changed:
                        movie["updated_on"] = movie.get("updated_on")
                        movie["rev"] = int(movie.get("rev", 0)) + 1
                        await active_db["movie"].replace_one({"_id": movie["_id"]}, movie)
            except Exception as e:
                result["errors"] += 1
                LOGGER.error("Error repairing dead movie links in DB %s: %s", i, e)

            try:
                tv_cursor = active_db["tv"].find({"seasons.episodes.telegram.is_dead": True})
                async for tv in tv_cursor:
                    changed = False
                    for season in tv.get("seasons", []):
                        for ep in season.get("episodes", []):
                            for quality in ep.get("telegram", []):
                                if not quality.get("is_dead"):
                                    continue
                                result["checked_dead"] += 1
                                alive = await self._check_file_alive(client, quality.get("id"))
                                if alive:
                                    quality.pop("is_dead", None)
                                    changed = True
                                    result["repaired"] += 1
                                    LOGGER.info(
                                        "Repaired dead flag for TV %s S%sE%s quality=%s name=%s",
                                        tv.get("tmdb_id"), season.get("season_number"),
                                        ep.get("episode_number"), quality.get("quality"), quality.get("name")
                                    )
                                else:
                                    result["still_dead"] += 1
                                await asyncio.sleep(0.25)
                    if changed:
                        tv["updated_on"] = tv.get("updated_on")
                        tv["rev"] = int(tv.get("rev", 0)) + 1
                        await active_db["tv"].replace_one({"_id": tv["_id"]}, tv)
            except Exception as e:
                result["errors"] += 1
                LOGGER.error("Error repairing dead TV links in DB %s: %s", i, e)

        return result

    async def _check_file_alive(self, client, quality_id: str) -> bool:
        """
        Decodes the quality_id to a Telegram chat_id and message_id,
        then attempts to fetch the message to see if it (and its media) still exist.
        """
        try:
            decoded = await decode_string(quality_id)
            if not decoded:
                return False

            # Normal stream: {chat_id, msg_id}
            # Split ZIP / split video stream: {type, parts:[{chat_id,msg_id,...}]}
            parts = decoded.get("parts") if isinstance(decoded, dict) else None
            if parts:
                # For split files, every part is required. Check all parts, but keep
                # the logic lightweight by fetching one Telegram message at a time.
                for part in parts:
                    part_chat = part.get("chat_id")
                    part_msg = part.get("msg_id")
                    if part_chat is None or part_msg is None:
                        return False
                    chat_id = int(part_chat)
                    if not str(chat_id).startswith("-100"):
                        chat_id = int(f"-100{chat_id}")
                    msg_id = int(part_msg)
                    messages = await client.get_messages(chat_id, message_ids=[msg_id])
                    msg = messages[0] if isinstance(messages, list) and messages else messages
                    if not msg or getattr(msg, "empty", False):
                        return False
                    if msg.document is None and msg.video is None and msg.audio is None:
                        return False
                return True

            if "chat_id" not in decoded or "msg_id" not in decoded:
                return False

            chat_id = int(decoded["chat_id"])
            if not str(chat_id).startswith("-100"):
                chat_id = int(f"-100{chat_id}")
            msg_id = int(decoded["msg_id"])

            # Use get_messages to fetch exactly one message
            messages = await client.get_messages(chat_id, message_ids=[msg_id])
            msg = messages[0] if isinstance(messages, list) and messages else messages
            if not msg or getattr(msg, "empty", False):
                return False
            if msg.document is None and msg.video is None and msg.audio is None:
                return False

            return True

        except Exception as e:
            # If the channel is banned, chat_id is invalid, or any other critical error occurs
            LOGGER.error(f"Link checker failed to resolve {quality_id}: {e}")
            return False
