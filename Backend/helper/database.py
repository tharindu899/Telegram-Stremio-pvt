import secrets
import string
from asyncio import create_task
from bson import ObjectId
import motor.motor_asyncio
from datetime import datetime, timezone
from pydantic import ValidationError
from pymongo import ASCENDING, DESCENDING
from typing import Dict, List, Optional, Tuple, Any

from Backend.logger import LOGGER
from Backend.config import Telegram
import re
from Backend.helper.encrypt import decode_string, encode_string
from Backend.helper.modal import Episode, MovieSchema, QualityDetail, Season, TVShowSchema
from Backend.helper.task_manager import delete_message
from Backend.helper.media_types import canonical_media_type


def convert_objectid_to_str(document: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in document.items():
        if isinstance(value, ObjectId):
            document[key] = str(value)
        elif isinstance(value, list):
            document[key] = [convert_objectid_to_str(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, dict):
            document[key] = convert_objectid_to_str(value)
    return document


class Database:
    def __init__(self, db_name: str = "dbFyvio"):
        self.db_uris = Telegram.DATABASE
        self.db_name = db_name

        # Hugging Face/Render may boot before secrets are configured.
        # Do not crash the whole web app; keep DB-dependent features disabled
        # until DATABASE contains at least 2 MongoDB URIs.
        self.disabled = len(self.db_uris) < 2

        self.clients: Dict[str, motor.motor_asyncio.AsyncIOMotorClient] = {}
        self.dbs: Dict[str, motor.motor_asyncio.AsyncIOMotorDatabase] = {}

        self.current_db_index = 1

    async def connect(self):
        if self.disabled:
            LOGGER.warning("DATABASE is not configured. Set at least 2 MongoDB URIs: tracking DB first, storage DB second.")
            return
        try:
            for index, uri in enumerate(self.db_uris):
                client = motor.motor_asyncio.AsyncIOMotorClient(uri)
                db_key = "tracking" if index == 0 else f"storage_{index}"
                self.clients[db_key] = client
                self.dbs[db_key] = client[self.db_name]
                db_type = "Tracking" if index == 0 else f"Storage {index}"

                masked_uri = re.sub(r"://(.*?):.*?@", r"://\1:*****@", uri)
                masked_uri = masked_uri.split('?')[0]
                
                LOGGER.info(f"{db_type} Database connected successfully: {masked_uri}")

            state = await self.dbs["tracking"]["state"].find_one({"_id": "db_index"})
            if not state:
                await self.dbs["tracking"]["state"].insert_one({"_id": "db_index", "current_index": 1})
                self.current_db_index = 1
            else:
                self.current_db_index = state["current_index"]

            LOGGER.info(f"Active storage DB: storage_{self.current_db_index}")

        except Exception as e:
            LOGGER.error(f"Database connection error: {e}")

    async def disconnect(self):
        for client in self.clients.values():
            client.close()
        LOGGER.info("All database connections closed.")

    async def update_current_db_index(self):
        await self.dbs["tracking"]["state"].update_one(
            {"_id": "db_index"},
            {"$set": {"current_index": self.current_db_index}},
            upsert=True
        )


    # -------------------------------
    # Runtime Config Management
    # -------------------------------
    async def get_runtime_config_values(self) -> Dict[str, Any]:
        if self.disabled or "tracking" not in self.dbs:
            return {}
        doc = await self.dbs["tracking"]["config"].find_one({"_id": "runtime_config"})
        values = dict((doc or {}).get("values", {}) or {})

        # Keep database documents clean when upgrading from older builds.
        # Old inline multi-bot values are no longer read. WebUI tokens now live
        # in a dedicated encrypted document; streaming parallelism/prefetch is automatic.
        from Backend.helper.runtime_config import LEGACY_RUNTIME_KEYS
        stale = [key for key in LEGACY_RUNTIME_KEYS if key in values]
        if stale:
            for key in stale:
                values.pop(key, None)
            try:
                await self.dbs["tracking"]["config"].update_one(
                    {"_id": "runtime_config"},
                    {
                        "$unset": {f"values.{key}": "" for key in stale},
                        "$set": {"updated_at": datetime.utcnow()},
                    },
                )
            except Exception:
                pass
        return values

    async def save_runtime_config_values(self, values: Dict[str, Any], updated_by: str = "webui") -> Dict[str, Any]:
        if self.disabled or "tracking" not in self.dbs:
            return {}
        from Backend.helper.runtime_config import CONFIG_SCHEMA, serialize_value
        clean = {}
        for key, value in (values or {}).items():
            if key in CONFIG_SCHEMA:
                clean[key] = serialize_value(key, value)
        await self.dbs["tracking"]["config"].update_one(
            {"_id": "runtime_config"},
            {"$set": {"values": clean, "updated_at": datetime.utcnow(), "updated_by": updated_by}},
            upsert=True,
        )
        if "AUTH_CHANNEL" in clean:
            channels = [x.strip() for x in str(clean.get("AUTH_CHANNEL") or "").replace("\n", ",").split(",") if x.strip()]
            await self.dbs["tracking"]["config"].update_one(
                {"_id": "auth_channels"},
                {"$set": {"channels": channels, "updated_at": datetime.utcnow(), "updated_by": updated_by}},
                upsert=True,
            )
        return clean

    # -------------------------------
    # WebUI Multi-Bot Token Management
    # -------------------------------
    async def get_multi_bot_tokens(self) -> List[str]:
        """Return decrypted WebUI-managed extra stream bot tokens.

        Tokens are never included in the normal runtime configuration document
        or returned through an API response.
        """
        if self.disabled or "tracking" not in self.dbs:
            return []
        doc = await self.dbs["tracking"]["config"].find_one({"_id": "multi_bot_tokens"})
        encrypted = (doc or {}).get("encrypted_tokens", "")
        if not encrypted:
            return []
        try:
            from Backend.helper.secure_tokens import decrypt_bot_tokens
            return decrypt_bot_tokens(encrypted)
        except Exception as exc:
            LOGGER.error(f"Could not load saved multi-bot tokens: {exc}")
            return []

    async def get_multi_bot_tokens_summary(self) -> Dict[str, Any]:
        """Return safe metadata only; token values never leave the server."""
        if self.disabled or "tracking" not in self.dbs:
            return {"saved_count": 0, "saved_bots": [], "updated_at": None}
        doc = await self.dbs["tracking"]["config"].find_one({"_id": "multi_bot_tokens"}) or {}
        tokens = await self.get_multi_bot_tokens()
        try:
            from Backend.helper.secure_tokens import token_id
            saved_bots = [
                {"id": token_id(token), "label": f"Stream Bot {index + 1}"}
                for index, token in enumerate(tokens)
            ]
        except Exception:
            saved_bots = []
        return {
            "saved_count": len(saved_bots),
            "saved_bots": saved_bots,
            "updated_at": doc.get("updated_at"),
        }

    async def save_multi_bot_tokens(self, tokens: List[str], updated_by: str = "webui") -> Dict[str, Any]:
        if self.disabled or "tracking" not in self.dbs:
            raise RuntimeError("Database is not connected yet.")
        from Backend.helper.secure_tokens import encrypt_bot_tokens, parse_bot_tokens
        clean_tokens = parse_bot_tokens(tokens)
        encrypted = encrypt_bot_tokens(clean_tokens)
        await self.dbs["tracking"]["config"].update_one(
            {"_id": "multi_bot_tokens"},
            {
                "$set": {
                    "encrypted_tokens": encrypted,
                    "count": len(clean_tokens),
                    "updated_at": datetime.utcnow(),
                    "updated_by": updated_by,
                }
            },
            upsert=True,
        )
        return await self.get_multi_bot_tokens_summary()

    async def remove_multi_bot_token(self, token_identifier: str, updated_by: str = "webui") -> Dict[str, Any]:
        from Backend.helper.secure_tokens import token_id
        tokens = await self.get_multi_bot_tokens()
        kept = [token for token in tokens if token_id(token) != str(token_identifier or "")]
        if len(kept) == len(tokens):
            raise ValueError("Saved stream bot was not found. Reload the page and try again.")
        return await self.save_multi_bot_tokens(kept, updated_by=updated_by)

    # -------------------------------
    # User Subscription Management
    # -------------------------------
    async def get_user(self, user_id: int) -> Optional[dict]:
        return await self.dbs["tracking"]["users"].find_one({"_id": user_id})

    async def update_user_interaction(self, user_id: int, first_name: str, username: str):
        await self.dbs["tracking"]["users"].update_one(
            {"_id": user_id},
            {"$set": {"first_name": first_name, "username": username, "last_interaction": datetime.utcnow()}},
            upsert=True
        )

    async def set_pending_payment(self, user_id: int, plan_duration: int, msg_id: int, price=0, admin_messages: list = None):
        update_data = {
            "pending_payment": {
                "duration": plan_duration,
                "price": price,
                "msg_id": msg_id,
                "date": datetime.utcnow(),
            }
        }
        if admin_messages is not None:
            update_data["pending_payment"]["admin_messages"] = admin_messages
        await self.dbs["tracking"]["users"].update_one(
            {"_id": user_id},
            {"$set": update_data},
            upsert=True
        )

    async def approve_payment(self, user_id: int) -> Optional[dict]:
        user = await self.get_user(user_id)
        if not user or "pending_payment" not in user:
            return None

        duration = user["pending_payment"]["duration"]
        
        # Calculate new expiry
        current_expiry = user.get("subscription_expiry")
        now = datetime.utcnow()
        if current_expiry and current_expiry > now:
            from datetime import timedelta
            new_expiry = current_expiry + timedelta(days=duration)
        else:
            from datetime import timedelta
            new_expiry = now + timedelta(days=duration)

        await self.dbs["tracking"]["users"].update_one(
            {"_id": user_id},
            {
                "$set": {"subscription_expiry": new_expiry, "subscription_status": "active"},
                "$unset": {"pending_payment": ""}
            }
        )
        return await self.get_user(user_id)

    async def reject_payment(self, user_id: int) -> bool:
        result = await self.dbs["tracking"]["users"].update_one(
            {"_id": user_id},
            {"$unset": {"pending_payment": ""}}
        )
        return result.modified_count > 0

    async def get_expired_users(self) -> List[dict]:
        cursor = self.dbs["tracking"]["users"].find({
            "subscription_expiry": {"$lt": datetime.utcnow()},
            "subscription_status": "active"
        })
        return await cursor.to_list(None)

    async def mark_user_expired(self, user_id: int):
        await self.dbs["tracking"]["users"].update_one(
            {"_id": user_id},
            {"$set": {"subscription_status": "expired"}}
        )

    async def get_expiring_users(self, hours: int = 24) -> List[dict]:
        from datetime import timedelta
        now = datetime.utcnow()
        target_time = now + timedelta(hours=hours)
        cursor = self.dbs["tracking"]["users"].find({
            "subscription_expiry": {"$gt": now, "$lte": target_time},
            "reminder_sent": {"$ne": True},
            "subscription_status": "active"
        })
        return await cursor.to_list(None)
        
    async def mark_reminder_sent(self, user_id: int):
         await self.dbs["tracking"]["users"].update_one(
            {"_id": user_id},
            {"$set": {"reminder_sent": True}}
        )

    # -------------------------------
    # Admin Subscription Management
    # -------------------------------
    async def get_subscription_plans(self) -> List[dict]:
        cursor = self.dbs["tracking"]["sub_plans"].find().sort("days", ASCENDING)
        plans = await cursor.to_list(None)
        return [convert_objectid_to_str(plan) for plan in plans]

    async def add_subscription_plan(self, days: int, price: float) -> Optional[str]:
        result = await self.dbs["tracking"]["sub_plans"].insert_one({
            "days": days,
            "price": price,
            "created_at": datetime.utcnow()
        })
        return str(result.inserted_id)

    async def update_subscription_plan(self, plan_id: str, days: int, price: float) -> bool:
        try:
            result = await self.dbs["tracking"]["sub_plans"].update_one(
                {"_id": ObjectId(plan_id)},
                {"$set": {"days": days, "price": price, "updated_at": datetime.utcnow()}}
            )
            return result.modified_count > 0
        except Exception:
            return False

    async def delete_subscription_plan(self, plan_id: str) -> bool:
        try:
            result = await self.dbs["tracking"]["sub_plans"].delete_one({"_id": ObjectId(plan_id)})
            return result.deleted_count > 0
        except Exception:
            return False

    async def get_all_subscribers(self) -> List[dict]:
        cursor = self.dbs["tracking"]["users"].find({
            "subscription_status": {"$in": ["active", "expired"]}
        }).sort("subscription_expiry", DESCENDING)
        users = await cursor.to_list(None)
        return [convert_objectid_to_str(u) for u in users]

    async def manage_subscriber(self, user_id: int, action: str, days: int = 0) -> bool:
        user = await self.get_user(user_id)
        if not user:
            return False
            
        now = datetime.utcnow()
        if action == "extend" or action == "reduce":
            from datetime import timedelta
            current_expiry = user.get("subscription_expiry")
            
            if action == "extend":
                if current_expiry and current_expiry > now:
                    new_expiry = current_expiry + timedelta(days=days)
                else:
                    new_expiry = now + timedelta(days=days)
            else: # reduce
                if current_expiry:
                    new_expiry = current_expiry - timedelta(days=days)
                    if new_expiry < now:
                        new_expiry = now # Just expire them
                else:
                    new_expiry = now # Already expired or none
            
            status = "active" if new_expiry > now else "expired"
            
            result = await self.dbs["tracking"]["users"].update_one(
                {"_id": user_id},
                {"$set": {"subscription_expiry": new_expiry, "subscription_status": status}}
            )
            return result.modified_count > 0
            
        elif action == "delete":
            result = await self.dbs["tracking"]["users"].update_one(
                {"_id": user_id},
                {"$unset": {"subscription_expiry": "", "subscription_status": ""}}
            )
            return result.modified_count > 0
            
        return False

    async def assign_subscription(self, user_id: int, days: int) -> dict:
        """Upsert a subscription for any user_id, creating a record if it doesn't exist."""
        from datetime import timedelta
        now = datetime.utcnow()

        user = await self.get_user(user_id)
        if user:
            current_expiry = user.get("subscription_expiry")
            if current_expiry and current_expiry > now:
                new_expiry = current_expiry + timedelta(days=days)
            else:
                new_expiry = now + timedelta(days=days)
        else:
            new_expiry = now + timedelta(days=days)

        await self.dbs["tracking"]["users"].update_one(
            {"_id": user_id},
            {
                "$set": {
                    "subscription_expiry": new_expiry,
                    "subscription_status": "active",
                },
                "$setOnInsert": {
                    "_id": user_id,
                    "first_name": f"User {user_id}",
                    "username": None,
                    "created_at": now,
                }
            },
            upsert=True
        )
        return {
            "user_id": user_id,
            "subscription_expiry": new_expiry.isoformat(),
            "subscription_status": "active",
            "days_assigned": days,
        }




    # -------------------------------
    # Custom Catalog Management
    # -------------------------------
    async def create_custom_catalog(self, name: str, visible: bool = True) -> Optional[str]:
        name = (name or "").strip()
        if not name:
            return None

        now = datetime.utcnow()
        result = await self.dbs["tracking"]["custom_catalogs"].insert_one({
            "name": name,
            "visible": bool(visible),
            "items": [],
            "created_at": now,
            "updated_at": now,
        })
        return str(result.inserted_id)

    async def get_custom_catalogs(self, visible_only: bool = False) -> List[dict]:
        query = {"visible": True} if visible_only else {}
        cursor = self.dbs["tracking"]["custom_catalogs"].find(query).sort("updated_at", DESCENDING)
        catalogs = await cursor.to_list(None)
        return [convert_objectid_to_str(catalog) for catalog in catalogs]

    async def get_custom_catalog(self, catalog_id: str) -> Optional[dict]:
        try:
            catalog = await self.dbs["tracking"]["custom_catalogs"].find_one({"_id": ObjectId(catalog_id)})
            return convert_objectid_to_str(catalog) if catalog else None
        except Exception:
            return None

    async def update_custom_catalog(self, catalog_id: str, name: Optional[str] = None, visible: Optional[bool] = None) -> bool:
        update_data = {"updated_at": datetime.utcnow()}
        if name is not None:
            clean_name = name.strip()
            if clean_name:
                update_data["name"] = clean_name
        if visible is not None:
            update_data["visible"] = bool(visible)

        try:
            result = await self.dbs["tracking"]["custom_catalogs"].update_one(
                {"_id": ObjectId(catalog_id)},
                {"$set": update_data}
            )
            return result.modified_count > 0
        except Exception:
            return False

    async def delete_custom_catalog(self, catalog_id: str) -> bool:
        try:
            result = await self.dbs["tracking"]["custom_catalogs"].delete_one({"_id": ObjectId(catalog_id)})
            return result.deleted_count > 0
        except Exception:
            return False

    async def add_item_to_custom_catalog(
        self, catalog_id: str, tmdb_id: int, db_index: int, media_type: str
    ) -> bool:
        media_type = canonical_media_type(media_type)
        item = {
            "tmdb_id": int(tmdb_id),
            "db_index": int(db_index),
            "media_type": media_type,
            "added_at": datetime.utcnow(),
        }
        try:
            result = await self.dbs["tracking"]["custom_catalogs"].update_one(
                {
                    "_id": ObjectId(catalog_id),
                    "items": {
                        "$not": {
                            "$elemMatch": {
                                "tmdb_id": int(tmdb_id),
                                "db_index": int(db_index),
                                "media_type": media_type,
                            }
                        }
                    },
                },
                {
                    "$push": {"items": {"$each": [item], "$position": 0}},
                    "$set": {"updated_at": datetime.utcnow()},
                }
            )
            return result.modified_count > 0
        except Exception:
            return False

    async def remove_item_from_custom_catalog(
        self, catalog_id: str, tmdb_id: int, db_index: int, media_type: str
    ) -> bool:
        media_type = canonical_media_type(media_type)
        try:
            result = await self.dbs["tracking"]["custom_catalogs"].update_one(
                {"_id": ObjectId(catalog_id)},
                {
                    "$pull": {
                        "items": {
                            "tmdb_id": int(tmdb_id),
                            "db_index": int(db_index),
                            "media_type": media_type,
                        }
                    },
                    "$set": {"updated_at": datetime.utcnow()},
                }
            )
            return result.modified_count > 0
        except Exception:
            return False

    async def custom_catalog_contains_item(
        self, catalog_id: str, tmdb_id: int, db_index: int, media_type: str
    ) -> bool:
        media_type = canonical_media_type(media_type)
        try:
            catalog = await self.dbs["tracking"]["custom_catalogs"].find_one({
                "_id": ObjectId(catalog_id),
                "items": {
                    "$elemMatch": {
                        "tmdb_id": int(tmdb_id),
                        "db_index": int(db_index),
                        "media_type": media_type,
                    }
                }
            })
            return bool(catalog)
        except Exception:
            return False

    async def get_custom_catalog_items(
        self, catalog_id: str, media_type: Optional[str] = None, page: int = 1, page_size: int = 24
    ) -> dict:
        catalog = await self.get_custom_catalog(catalog_id)
        if not catalog:
            return {"catalog": None, "items": [], "total_count": 0, "current_page": page, "total_pages": 0}

        db_media_type = None
        if media_type:
            db_media_type = canonical_media_type(media_type)

        raw_items = catalog.get("items", []) or []
        if db_media_type:
            raw_items = [item for item in raw_items if item.get("media_type") == db_media_type]

        total_count = len(raw_items)
        skip = (page - 1) * page_size
        selected_items = raw_items[skip:skip + page_size]

        hydrated_items = []
        for item in selected_items:
            doc = await self.get_document(
                item.get("media_type", "movie"),
                int(item.get("tmdb_id")),
                int(item.get("db_index", 1))
            )
            if doc:
                hydrated_items.append(doc)

        total_pages = (total_count + page_size - 1) // page_size if total_count else 0
        return {
            "catalog": catalog,
            "items": hydrated_items,
            "total_count": total_count,
            "current_page": page,
            "total_pages": total_pages,
        }


    # -------------------------------
    # Helper Methods for Repeated Logic
    # -------------------------------
    def _get_sort_dict(self, sort_params: List[Tuple[str, str]]) -> Dict[str, int]:
        if sort_params:
            sort_field, sort_direction = sort_params[0]
            return {sort_field: DESCENDING if sort_direction.lower() == "desc" else ASCENDING}
        return {"updated_on": DESCENDING}

    async def _paginate_collection(
        self,
        collection_name: str,
        sort_dict: Dict[str, int],
        page: int,
        page_size: int,
        filter_dict: Optional[dict] = None
    ):
        filter_dict = filter_dict or {}
        skip = (page - 1) * page_size
        results = []
        dbs_checked = []
        total_count = 0

        db_counts = []
        for i in range(1, self.current_db_index + 1):
            db_key = f"storage_{i}"
            db = self.dbs[db_key]
            count = await db[collection_name].count_documents(filter_dict)
            db_counts.append((i, count))
            total_count += count

        start_db_index = None
        for db_index, count in reversed(db_counts):
            if skip < count:
                start_db_index = db_index
                break
            skip -= count

        if not start_db_index:
            return [], [], total_count

        for db_index, count in reversed(db_counts):
            if db_index < start_db_index:
                continue

            db_key = f"storage_{db_index}"
            db = self.dbs[db_key]
            dbs_checked.append(db_index)

            cursor = (
                db[collection_name]
                .find(filter_dict)
                .sort(sort_dict)
                .skip(skip if db_index == start_db_index else 0)
                .limit(page_size - len(results))
            )

            docs = await cursor.to_list(None)
            results.extend(docs)

            if len(results) >= page_size:
                break

        return results, dbs_checked, total_count

    async def _move_document(
        self, collection_name: str, document: dict, old_db_index: int
    ) -> bool:
        current_db_key = f"storage_{self.current_db_index}"
        old_db_key = f"storage_{old_db_index}"
        document["db_index"] = self.current_db_index
        try:
            await self.dbs[current_db_key][collection_name].insert_one(document)
            await self.dbs[old_db_key][collection_name].delete_one({"_id": document["_id"]})
            LOGGER.info(f"✅ Moved document {document.get('tmdb_id')} from {old_db_key} to {current_db_key}")
            return True
        except Exception as e:
            LOGGER.error(f"Error moving document to {current_db_key}: {e}")
            return False

    @staticmethod
    def _cas_filter(doc_id: ObjectId, current_rev: int) -> dict:
        """
        Build the filter for a compare-and-swap replace_one(): it only matches
        if `rev` is still exactly what we read, so a stale in-memory copy can
        never blindly overwrite a write that landed after our read.

        Documents created before this field existed have no `rev` at all --
        current_rev defaults to 0 for those via .get("rev", 0), so the filter
        also accepts "rev is missing" in that case. The first successful
        write sets rev=1 and the document uses plain equality from then on.
        """
        if current_rev == 0:
            return {"_id": doc_id, "$or": [{"rev": 0}, {"rev": {"$exists": False}}]}
        return {"_id": doc_id, "rev": current_rev}

    @staticmethod
    def _coerce_datetime(value) -> Optional[datetime]:
        """Normalize stored/upload timestamps without ever using rescan time as add date."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                return None
        return None

    def _quality_added_on(self, quality: dict | None) -> Optional[datetime]:
        if not isinstance(quality, dict):
            return None
        return self._coerce_datetime(quality.get("date_added") or quality.get("added_on"))

    def _movie_latest_added_on(self, movie: dict, fallback=None) -> datetime:
        dates = [self._quality_added_on(q) for q in (movie.get("telegram") or [])]
        dates = [d for d in dates if d]
        if dates:
            return max(dates)
        return self._coerce_datetime(fallback) or self._coerce_datetime(movie.get("updated_on")) or datetime.utcnow()

    def _tv_latest_added_on(self, tv: dict, fallback=None) -> datetime:
        dates = []
        for season in tv.get("seasons") or []:
            for episode in season.get("episodes") or []:
                for q in episode.get("telegram") or []:
                    dt = self._quality_added_on(q)
                    if dt:
                        dates.append(dt)
        if dates:
            return max(dates)
        return self._coerce_datetime(fallback) or self._coerce_datetime(tv.get("updated_on")) or datetime.utcnow()

    def _movie_first_added_on(self, movie: dict, fallback=None) -> datetime:
        dates = [self._quality_added_on(q) for q in (movie.get("telegram") or [])]
        dates = [d for d in dates if d]
        if dates:
            return min(dates)
        return self._coerce_datetime(movie.get("added_on")) or self._coerce_datetime(fallback) or self._coerce_datetime(movie.get("updated_on")) or datetime.utcnow()

    def _tv_first_added_on(self, tv: dict, fallback=None) -> datetime:
        dates = []
        for season in tv.get("seasons") or []:
            for episode in season.get("episodes") or []:
                for q in episode.get("telegram") or []:
                    dt = self._quality_added_on(q)
                    if dt:
                        dates.append(dt)
        if dates:
            return min(dates)
        return self._coerce_datetime(tv.get("added_on")) or self._coerce_datetime(fallback) or self._coerce_datetime(tv.get("updated_on")) or datetime.utcnow()

    async def _schedule_stream_delete(self, stream_id: str) -> None:
        """Queue deletion for a normal Telegram file or every part of a split stream."""
        try:
            decoded = await decode_string(stream_id)
            if decoded.get("type") in {"split_zip", "split_file"}:
                for part in decoded.get("parts", []):
                    try:
                        chat_id = int(f"-100{part['chat_id']}")
                        msg_id = int(part["msg_id"])
                        create_task(delete_message(chat_id, msg_id))
                    except Exception as part_error:
                        LOGGER.error(f"Failed to queue split part deletion: {part_error}")
                return

            chat_id = int(f"-100{decoded['chat_id']}")
            msg_id = int(decoded['msg_id'])
            create_task(delete_message(chat_id, msg_id))
        except Exception as e:
            LOGGER.error(f"Failed to queue stream deletion: {e}")

    async def _schedule_quality_deletes(self, old_ids: List[str]) -> None:
        """
        Fire Telegram delete_message tasks for quality entries that got
        replaced (REPLACE_MODE). Call this only *after* the corresponding
        DB write has actually been persisted -- otherwise a write that gets
        retried or aborted due to a CAS conflict would schedule a delete for
        a message that's still the only copy on record.
        """
        for old_id in old_ids:
            await self._schedule_stream_delete(old_id)

    @staticmethod
    def _dedupe_name_key(name: str) -> str:
        """Stable duplicate key for re-sent files/subtitles.

        The key is intentionally strict enough to keep different releases
        (PSA/GalaxyTV/BluRay/WebRip etc.) but forgiving enough to catch the
        same file re-sent with small caption/name differences, source labels,
        or split wrappers.
        """
        text = (name or "").strip().lower()
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"(?i)^\s*(file|filename|source|subtitle|sub)\s*[:：-]\s*", " ", text)
        text = re.sub(r"(?i)\s*\[(split zip|split video|split file)\s*x?\d+\]\s*", " ", text)
        text = re.sub(r"(?i)\.(zip|mkv|mp4|avi|mov|webm|srt|vtt|ass|ssa|sub)\.0*\d+$", r".\1", text)
        text = re.sub(r"[\s_]+", " ", text)
        text = re.sub(r"[\[\]{}()]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip(" .-_")

    def _same_stream_release(self, old: dict | None, new: dict | None) -> bool:
        """True only when a video stream is the same release re-sent.

        Older code replaced every stream with the same quality (for example any
        720p episode). That deleted/removed different releases such as PSA and
        GalaxyTV. This matches exact filename/release instead, while still
        letting a resend of the same file replace the previous Telegram message.
        """
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if old.get("id") and old.get("id") == new.get("id"):
            return True
        if old.get("quality") != new.get("quality"):
            return False
        if self._dedupe_name_key(old.get("name")) != self._dedupe_name_key(new.get("name")):
            return False
        old_size = str(old.get("size") or "").strip().lower()
        new_size = str(new.get("size") or "").strip().lower()
        # If both sizes are known, require them to match. If one old entry lacks
        # size from a previous build, filename + quality is enough.
        if old_size and new_size and old_size != new_size:
            return False
        return True

    def _stream_ids_for_message(self, doc: dict, channel: int, msg_id: int) -> list[str]:
        """Return stream/subtitle IDs in a DB document that point to chat/msg.

        Supports normal streams, split ZIP/video streams (parts array), and
        subtitles. This makes Telegram delete events clean DB rows even when
        only one part of a split upload was deleted.
        """
        matched: list[str] = []
        channel = int(channel)
        msg_id = int(msg_id)

        def add_if_match(item: dict | None):
            if not isinstance(item, dict):
                return
            sid = item.get("id")
            if not sid:
                return
            try:
                decoded = None
                # Direct ids decode to {chat_id, msg_id}; split ids often also
                # keep a parts array in the DB item, so check both.
                try:
                    decoded = None
                    # decode_string is async, so only inspect stored parts here;
                    # direct ID path is handled by exact hash in delete method.
                except Exception:
                    decoded = None
                for part in item.get("parts") or []:
                    if int(part.get("chat_id")) == channel and int(part.get("msg_id")) == msg_id:
                        matched.append(sid)
                        return
            except Exception:
                return

        for q in doc.get("telegram") or []:
            add_if_match(q)
        for sub in doc.get("subtitles") or []:
            add_if_match(sub)
        for season in doc.get("seasons") or []:
            for episode in season.get("episodes") or []:
                for q in episode.get("telegram") or []:
                    add_if_match(q)
                for sub in episode.get("subtitles") or []:
                    add_if_match(sub)
        return matched

    async def delete_media_by_message(self, channel: int, msg_id: int) -> bool:
        """Remove DB entries that point to a Telegram chat/message.

        Covers:
          - normal movie/series video streams
          - split ZIP/direct split-video entries when any part is deleted
          - movie/series subtitle entries

        This is used by Telegram delete events so re-sending a replacement file
        does not leave dead duplicates in Stremio.
        """
        removed_any = False
        channel = int(str(channel).replace("-100", "", 1))
        msg_id = int(msg_id)

        try:
            direct_hash = await encode_string({"chat_id": channel, "msg_id": msg_id})
        except Exception:
            direct_hash = None

        for i in range(1, self.current_db_index + 1):
            db = self.dbs.get(f"storage_{i}")
            if db is None:
                continue

            # Movies: direct stream/subtitle ids or split parts.
            movie_candidates = []
            if direct_hash:
                movie_candidates.extend(await db["movie"].find({
                    "$or": [
                        {"telegram.id": direct_hash},
                        {"subtitles.id": direct_hash},
                    ]
                }).to_list(None))
            movie_candidates.extend(await db["movie"].find({
                "$or": [
                    {"telegram.parts": {"$elemMatch": {"chat_id": channel, "msg_id": msg_id}}},
                    {"subtitles.parts": {"$elemMatch": {"chat_id": channel, "msg_id": msg_id}}},
                ]
            }).to_list(None))

            seen_movie_ids = set()
            for movie in movie_candidates:
                mid = movie.get("_id")
                if mid in seen_movie_ids:
                    continue
                seen_movie_ids.add(mid)

                before_streams = len(movie.get("telegram") or [])
                before_subs = len(movie.get("subtitles") or [])
                movie["telegram"] = [
                    q for q in (movie.get("telegram") or [])
                    if not (
                        (direct_hash and q.get("id") == direct_hash)
                        or any(int(p.get("chat_id")) == channel and int(p.get("msg_id")) == msg_id for p in (q.get("parts") or []))
                    )
                ]
                movie["subtitles"] = [
                    sub for sub in (movie.get("subtitles") or [])
                    if not (direct_hash and sub.get("id") == direct_hash)
                ]

                if len(movie.get("telegram") or []) != before_streams or len(movie.get("subtitles") or []) != before_subs:
                    removed_any = True
                    if not movie.get("telegram") and not movie.get("subtitles"):
                        await db["movie"].delete_one({"_id": movie["_id"]})
                    else:
                        movie["updated_on"] = self._movie_latest_added_on(movie, movie.get("updated_on"))
                        movie["rev"] = int(movie.get("rev", 0)) + 1
                        await db["movie"].replace_one({"_id": movie["_id"]}, movie)

            # TV: direct stream/subtitle ids or split parts.
            tv_candidates = []
            if direct_hash:
                tv_candidates.extend(await db["tv"].find({
                    "$or": [
                        {"seasons.episodes.telegram.id": direct_hash},
                        {"seasons.episodes.subtitles.id": direct_hash},
                    ]
                }).to_list(None))
            tv_candidates.extend(await db["tv"].find({
                "$or": [
                    {"seasons.episodes.telegram.parts": {"$elemMatch": {"chat_id": channel, "msg_id": msg_id}}},
                    {"seasons.episodes.subtitles.parts": {"$elemMatch": {"chat_id": channel, "msg_id": msg_id}}},
                ]
            }).to_list(None))

            seen_tv_ids = set()
            for tv in tv_candidates:
                tid = tv.get("_id")
                if tid in seen_tv_ids:
                    continue
                seen_tv_ids.add(tid)

                changed = False
                for season in tv.get("seasons") or []:
                    kept_episodes = []
                    for episode in season.get("episodes") or []:
                        before_streams = len(episode.get("telegram") or [])
                        before_subs = len(episode.get("subtitles") or [])
                        episode["telegram"] = [
                            q for q in (episode.get("telegram") or [])
                            if not (
                                (direct_hash and q.get("id") == direct_hash)
                                or any(int(p.get("chat_id")) == channel and int(p.get("msg_id")) == msg_id for p in (q.get("parts") or []))
                            )
                        ]
                        episode["subtitles"] = [
                            sub for sub in (episode.get("subtitles") or [])
                            if not (direct_hash and sub.get("id") == direct_hash)
                        ]
                        if len(episode.get("telegram") or []) != before_streams or len(episode.get("subtitles") or []) != before_subs:
                            changed = True
                        if episode.get("telegram") or episode.get("subtitles"):
                            kept_episodes.append(episode)
                    season["episodes"] = kept_episodes

                tv["seasons"] = [s for s in (tv.get("seasons") or []) if s.get("episodes")]
                if changed:
                    removed_any = True
                    if not tv.get("seasons"):
                        await db["tv"].delete_one({"_id": tv["_id"]})
                    else:
                        tv["updated_on"] = self._tv_latest_added_on(tv, tv.get("updated_on"))
                        tv["rev"] = int(tv.get("rev", 0)) + 1
                        await db["tv"].replace_one({"_id": tv["_id"]}, tv)

        return removed_any

    async def preview_duplicate_entries(self) -> dict:
        """Count exact duplicate streams/subtitles without changing DB.

        Uses the same duplicate key logic as remove_duplicate_entries():
          - video stream: quality + normalized filename
          - subtitle: language + format + normalized filename
        """
        stats = {"movies": 0, "episodes": 0, "subtitles": 0, "old_messages_queued": 0}

        def count_dupes(items: list, key_func):
            seen = set()
            dupes = 0
            for item in reversed(items or []):
                key = key_func(item)
                if not key:
                    continue
                if key in seen:
                    dupes += 1
                else:
                    seen.add(key)
            return dupes

        for i in range(1, self.current_db_index + 1):
            storage = self.dbs.get(f"storage_{i}")
            if storage is None:
                continue

            async for movie in storage["movie"].find({}):
                stats["movies"] += count_dupes(
                    movie.get("telegram") or [],
                    lambda q: (q.get("quality"), self._dedupe_name_key(q.get("name")))
                )
                stats["subtitles"] += count_dupes(
                    movie.get("subtitles") or [],
                    lambda sub: (sub.get("language"), sub.get("format"), self._dedupe_name_key(sub.get("name")))
                )

            async for tv in storage["tv"].find({}):
                for season in tv.get("seasons") or []:
                    for episode in season.get("episodes") or []:
                        stats["episodes"] += count_dupes(
                            episode.get("telegram") or [],
                            lambda q: (q.get("quality"), self._dedupe_name_key(q.get("name")))
                        )
                        stats["subtitles"] += count_dupes(
                            episode.get("subtitles") or [],
                            lambda sub: (sub.get("language"), sub.get("format"), self._dedupe_name_key(sub.get("name")))
                        )

        stats["old_messages_queued"] = stats["movies"] + stats["episodes"] + stats["subtitles"]
        return stats

    async def remove_duplicate_entries(self, delete_old_messages: bool = True) -> dict:
        """Remove exact duplicate streams/subtitles already stored in DB.

        Keeps the newest/last entry and removes older duplicates. Duplicate key:
          - video stream: quality + normalized filename
          - subtitle: language + format + normalized filename
        """
        stats = {"movies": 0, "episodes": 0, "subtitles": 0, "old_messages_queued": 0}

        def dedupe_list(items: list, key_func):
            seen = {}
            remove_ids = set()
            old_stream_ids = []
            # iterate reversed so newest appended item is kept
            for item in reversed(items or []):
                key = key_func(item)
                if not key:
                    continue
                if key in seen:
                    sid = item.get("id")
                    if sid:
                        remove_ids.add(sid)
                        old_stream_ids.append(sid)
                else:
                    seen[key] = item.get("id")
            kept = [item for item in (items or []) if item.get("id") not in remove_ids]
            return kept, old_stream_ids

        for i in range(1, self.current_db_index + 1):
            db = self.dbs.get(f"storage_{i}")
            if db is None:
                continue

            async for movie in db["movie"].find({}):
                changed = False
                telegram, old_ids = dedupe_list(
                    movie.get("telegram") or [],
                    lambda q: (q.get("quality"), self._dedupe_name_key(q.get("name")))
                )
                if old_ids:
                    stats["movies"] += len(old_ids)
                    movie["telegram"] = telegram
                    changed = True
                    if delete_old_messages:
                        for sid in old_ids:
                            await self._schedule_stream_delete(sid)
                        stats["old_messages_queued"] += len(old_ids)

                subtitles, old_sub_ids = dedupe_list(
                    movie.get("subtitles") or [],
                    lambda sub: (sub.get("language"), sub.get("format"), self._dedupe_name_key(sub.get("name")))
                )
                if old_sub_ids:
                    stats["subtitles"] += len(old_sub_ids)
                    movie["subtitles"] = subtitles
                    changed = True
                    if delete_old_messages:
                        for sid in old_sub_ids:
                            await self._schedule_stream_delete(sid)
                        stats["old_messages_queued"] += len(old_sub_ids)

                if changed:
                    movie["updated_on"] = self._movie_latest_added_on(movie, movie.get("updated_on"))
                    movie["rev"] = int(movie.get("rev", 0)) + 1
                    if movie.get("telegram") or movie.get("subtitles"):
                        await db["movie"].replace_one({"_id": movie["_id"]}, movie)
                    else:
                        await db["movie"].delete_one({"_id": movie["_id"]})

            async for tv in db["tv"].find({}):
                changed = False
                for season in tv.get("seasons") or []:
                    for episode in season.get("episodes") or []:
                        telegram, old_ids = dedupe_list(
                            episode.get("telegram") or [],
                            lambda q: (q.get("quality"), self._dedupe_name_key(q.get("name")))
                        )
                        if old_ids:
                            stats["episodes"] += len(old_ids)
                            episode["telegram"] = telegram
                            changed = True
                            if delete_old_messages:
                                for sid in old_ids:
                                    await self._schedule_stream_delete(sid)
                                stats["old_messages_queued"] += len(old_ids)

                        subtitles, old_sub_ids = dedupe_list(
                            episode.get("subtitles") or [],
                            lambda sub: (sub.get("language"), sub.get("format"), self._dedupe_name_key(sub.get("name")))
                        )
                        if old_sub_ids:
                            stats["subtitles"] += len(old_sub_ids)
                            episode["subtitles"] = subtitles
                            changed = True
                            if delete_old_messages:
                                for sid in old_sub_ids:
                                    await self._schedule_stream_delete(sid)
                                stats["old_messages_queued"] += len(old_sub_ids)

                if changed:
                    tv["seasons"] = [
                        s for s in (tv.get("seasons") or [])
                        if any((e.get("telegram") or e.get("subtitles")) for e in s.get("episodes") or [])
                    ]
                    for season in tv.get("seasons") or []:
                        season["episodes"] = [e for e in season.get("episodes") or [] if e.get("telegram") or e.get("subtitles")]
                    tv["updated_on"] = self._tv_latest_added_on(tv, tv.get("updated_on"))
                    tv["rev"] = int(tv.get("rev", 0)) + 1
                    if tv.get("seasons"):
                        await db["tv"].replace_one({"_id": tv["_id"]}, tv)
                    else:
                        await db["tv"].delete_one({"_id": tv["_id"]})

        return stats

    async def _handle_storage_error(self, func, *args, total_storage_dbs: int) -> Optional[Any]:
        next_db_index = (self.current_db_index % total_storage_dbs) + 1
        if next_db_index == 1:
            LOGGER.warning("⚠️ All storage databases are full! Add more.")
            return None
        self.current_db_index = next_db_index
        await self.update_current_db_index()
        LOGGER.info(f"Switched to storage_{self.current_db_index}")
        return await func(*args)

    # -------------------------------
    # Multi Database Method for insert/update/delete/list
    # -------------------------------

    async def insert_media(
        self, metadata_info: dict,
        channel: int, msg_id: int, size: str, name: str
    ) -> Optional[ObjectId]:
        # Keep storage stable even when a provider reports tvMovie/tvMiniSeries.
        metadata_info = dict(metadata_info or {})
        metadata_info["media_type"] = canonical_media_type(metadata_info.get("media_type"))

        # Use the original Telegram message date as the media add date.
        # This keeps Stremio latest movie/series order stable after /scan or /rescan.
        date_added = self._coerce_datetime(metadata_info.get("date_added")) or datetime.utcnow()
        quality_kwargs = {
            "quality": metadata_info['quality'],
            "id": metadata_info['encoded_string'],
            "name": name,
            "size": size,
            "date_added": date_added,
        }
        for optional_key in ("source_type", "archive_name", "part_count", "parts", "source_chat_id", "source_topic_id", "source_link", "release_group"):
            if optional_key in metadata_info:
                quality_kwargs[optional_key] = metadata_info[optional_key]
        
        if metadata_info['media_type'] == "movie":
            media = MovieSchema(
                tmdb_id=metadata_info['tmdb_id'],
                imdb_id=metadata_info['imdb_id'],
                db_index=self.current_db_index,
                title=metadata_info['title'],
                genres=metadata_info['genres'],
                description=metadata_info['description'],
                rating=metadata_info['rate'],
                release_year=metadata_info['year'],
                poster=metadata_info['poster'],
                backdrop=metadata_info['backdrop'],
                logo=metadata_info['logo'],
                cast=metadata_info['cast'],
                runtime=metadata_info['runtime'],
                media_type=metadata_info['media_type'],
                added_on=date_added,
                updated_on=date_added,
                telegram=[QualityDetail(**quality_kwargs)]
            )
            return await self.update_movie(media)
        else:
            tv_show = TVShowSchema(
                tmdb_id=metadata_info['tmdb_id'],
                imdb_id=metadata_info['imdb_id'],
                db_index=self.current_db_index,
                title=metadata_info['title'],
                genres=metadata_info['genres'],
                description=metadata_info['description'],
                rating=metadata_info['rate'],
                release_year=metadata_info['year'],
                poster=metadata_info['poster'],
                backdrop=metadata_info['backdrop'],
                logo=metadata_info['logo'],
                cast=metadata_info['cast'],
                runtime=metadata_info['runtime'],
                media_type=metadata_info['media_type'],
                added_on=date_added,
                updated_on=date_added,
                seasons=[Season(
                    season_number=metadata_info['season_number'],
                    episodes=[Episode(
                        episode_number=metadata_info['episode_number'],
                        title=metadata_info['episode_title'],
                        episode_backdrop=metadata_info['episode_backdrop'],
                        overview=metadata_info['episode_overview'],
                        released=metadata_info['episode_released'],
                        telegram=[QualityDetail(**quality_kwargs)]
                    )]
                )]
            )
            return await self.update_tv_show(tv_show)

    async def update_movie(self, movie_data: MovieSchema) -> Optional[ObjectId]:
        try:
            movie_dict = movie_data.dict()
        except ValidationError as e:
            LOGGER.error(f"Validation error: {e}")
            return None

        imdb_id = movie_dict["imdb_id"]
        tmdb_id = movie_dict["tmdb_id"]
        title = movie_dict["title"]
        release_year = movie_dict["release_year"]

        quality_to_update = movie_dict["telegram"][0]
        incoming_added_on = self._quality_added_on(quality_to_update) or self._coerce_datetime(movie_dict.get("updated_on")) or datetime.utcnow()
        quality_to_update["date_added"] = incoming_added_on

        current_db_key = f"storage_{self.current_db_index}"
        total_storage_dbs = len(self.dbs) - 1

        MAX_CAS_RETRIES = 5

        for attempt in range(MAX_CAS_RETRIES):
            existing_movie = None
            existing_db_key = None
            existing_db_index = None

            for db_index in range(1, total_storage_dbs + 1):
                db_key = f"storage_{db_index}"
                movie = None

                if imdb_id:
                    movie = await self.dbs[db_key]["movie"].find_one({"imdb_id": imdb_id})
                if not movie and tmdb_id:
                    movie = await self.dbs[db_key]["movie"].find_one({"tmdb_id": tmdb_id})
                if not movie and title and release_year:
                    movie = await self.dbs[db_key]["movie"].find_one({
                        "title": title,
                        "release_year": release_year
                    })

                if movie:
                    existing_movie = movie
                    existing_db_key = db_key
                    existing_db_index = db_index
                    break

            # ---------------- INSERT NEW MOVIE ----------------
            if not existing_movie:
                try:
                    movie_dict["db_index"] = self.current_db_index
                    movie_dict["rev"] = 0
                    movie_dict.setdefault("added_on", incoming_added_on)
                    movie_dict["updated_on"] = incoming_added_on
                    result = await self.dbs[current_db_key]["movie"].insert_one(movie_dict)
                    return result.inserted_id
                except Exception as e:
                    LOGGER.error(f"Insertion failed in {current_db_key}: {e}")
                    if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                        return await self._handle_storage_error(self.update_movie, movie_data, total_storage_dbs=total_storage_dbs)
                    return None

            # ---------------- UPDATE MOVIE ----------------
            movie_id = existing_movie["_id"]
            current_rev = existing_movie.get("rev", 0)
            existing_qualities = existing_movie.get("telegram", [])
            ids_to_delete: List[str] = []
            changed = False

            # Backfill date_added for already-indexed messages during rescan without moving order to "now".
            for old_q in existing_qualities:
                if old_q.get("id") == quality_to_update.get("id") and not old_q.get("date_added"):
                    old_q["date_added"] = incoming_added_on
                    changed = True

            # Always replace an exact same file/release when it is re-sent with
            # a new Telegram message id. This prevents duplicate Stremio streams
            # even if REPLACE_MODE is disabled in WebUI. Different releases with
            # the same quality are still kept because _same_stream_release()
            # matches by normalized file/release name, not by quality alone.
            if any(q.get("id") == quality_to_update.get("id") for q in existing_qualities):
                pass
            else:
                to_delete = [q for q in existing_qualities if self._same_stream_release(q, quality_to_update)]
                ids_to_delete = [q.get("id") for q in to_delete if q.get("id")]
                if to_delete:
                    existing_qualities = [
                        q for q in existing_qualities if not self._same_stream_release(q, quality_to_update)
                    ]
                    existing_qualities.append(quality_to_update)
                    changed = True
                else:
                    existing_qualities.append(quality_to_update)
                    changed = True

            if not changed:
                return movie_id

            existing_movie["telegram"] = existing_qualities
            existing_movie["added_on"] = self._movie_first_added_on(existing_movie, incoming_added_on)
            # Latest order follows the latest Telegram upload date, not rescan time.
            existing_movie["updated_on"] = self._movie_latest_added_on(existing_movie, incoming_added_on)
            existing_movie["rev"] = current_rev + 1

            if existing_db_index != self.current_db_index:
                try:
                    if await self._move_document("movie", existing_movie, existing_db_index):
                        await self._schedule_quality_deletes(ids_to_delete)
                        return movie_id
                except Exception as e:
                    LOGGER.error(f"Error moving movie to {current_db_key}: {e}")
                    if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                        return await self._handle_storage_error(self.update_movie, movie_data, total_storage_dbs=total_storage_dbs)
                # Move failed without a storage/quota error -> fall through and
                # persist in place at the old shard instead (original fallback).

            try:
                cas_result = await self.dbs[existing_db_key]["movie"].replace_one(
                    self._cas_filter(movie_id, current_rev), existing_movie
                )
            except Exception as e:
                LOGGER.error(f"Failed to update movie {tmdb_id} in {existing_db_key}: {e}")
                if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                    return await self._handle_storage_error(self.update_movie, movie_data, total_storage_dbs=total_storage_dbs)
                return None

            if cas_result.matched_count:
                await self._schedule_quality_deletes(ids_to_delete)
                return movie_id

            LOGGER.warning(
                f"CAS conflict updating movie {tmdb_id} -- concurrent write detected, "
                f"retrying ({attempt + 1}/{MAX_CAS_RETRIES})"
            )

        LOGGER.error(f"Gave up updating movie {tmdb_id} after {MAX_CAS_RETRIES} CAS retries (high write contention).")
        return None

    async def update_tv_show(self, tv_show_data: TVShowSchema) -> Optional[ObjectId]:
        try:
            tv_show_dict = tv_show_data.dict()
        except ValidationError as e:
            LOGGER.error(f"Validation error: {e}")
            return None

        imdb_id = tv_show_dict.get("imdb_id")
        tmdb_id = tv_show_dict.get("tmdb_id")
        title = tv_show_dict["title"]
        release_year = tv_show_dict["release_year"]

        incoming_dates: List[datetime] = []
        for season in tv_show_dict.get("seasons") or []:
            for episode in season.get("episodes") or []:
                for quality in episode.get("telegram") or []:
                    dt = self._quality_added_on(quality) or self._coerce_datetime(tv_show_dict.get("updated_on")) or datetime.utcnow()
                    quality["date_added"] = dt
                    incoming_dates.append(dt)
        incoming_added_on = max(incoming_dates) if incoming_dates else (self._coerce_datetime(tv_show_dict.get("updated_on")) or datetime.utcnow())

        current_db_key = f"storage_{self.current_db_index}"
        total_storage_dbs = len(self.dbs) - 1

        MAX_CAS_RETRIES = 5

        for attempt in range(MAX_CAS_RETRIES):
            existing_tv = None
            existing_db_key = None
            existing_db_index = None

            for db_index in range(1, total_storage_dbs + 1):
                db_key = f"storage_{db_index}"
                tv = None

                if imdb_id:
                    tv = await self.dbs[db_key]["tv"].find_one({"imdb_id": imdb_id})
                if not tv and tmdb_id:
                    tv = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
                if not tv and title and release_year:
                    tv = await self.dbs[db_key]["tv"].find_one({
                        "title": title,
                        "release_year": release_year
                    })

                if tv:
                    existing_tv = tv
                    existing_db_key = db_key
                    existing_db_index = db_index
                    break

            # ---------------- INSERT NEW TV ----------------
            if not existing_tv:
                try:
                    tv_show_dict["db_index"] = self.current_db_index
                    tv_show_dict["rev"] = 0
                    tv_show_dict.setdefault("added_on", incoming_added_on)
                    tv_show_dict["updated_on"] = incoming_added_on
                    result = await self.dbs[current_db_key]["tv"].insert_one(tv_show_dict)
                    return result.inserted_id
                except Exception as e:
                    LOGGER.error(f"Insertion failed in {current_db_key}: {e}")
                    if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                        return await self._handle_storage_error(self.update_tv_show, tv_show_data, total_storage_dbs=total_storage_dbs)
                    return None

            # ---------------- UPDATE TV ----------------
            tv_id = existing_tv["_id"]
            current_rev = existing_tv.get("rev", 0)
            ids_to_delete: List[str] = []
            changed = False

            for season in tv_show_dict["seasons"]:
                existing_season = next(
                    (s for s in existing_tv["seasons"]
                    if s["season_number"] == season["season_number"]),
                    None
                )

                if not existing_season:
                    existing_tv["seasons"].append(season)
                    changed = True
                    continue

                for episode in season["episodes"]:
                    existing_episode = next(
                        (e for e in existing_season["episodes"]
                        if e["episode_number"] == episode["episode_number"]),
                        None
                    )

                    if not existing_episode:
                        existing_season["episodes"].append(episode)
                        changed = True
                        continue

                    existing_episode.setdefault("telegram", [])

                    for quality in episode["telegram"]:
                        q_added_on = self._quality_added_on(quality) or incoming_added_on
                        quality["date_added"] = q_added_on

                        for old_q in existing_episode["telegram"]:
                            if old_q.get("id") == quality.get("id") and not old_q.get("date_added"):
                                old_q["date_added"] = q_added_on
                                changed = True

                        # Always replace an exact same file/release when it is re-sent with
                        # a new Telegram message id. This prevents duplicate episode streams
                        # while keeping different releases of the same quality.
                        if any(q.get("id") == quality.get("id") for q in existing_episode["telegram"]):
                            pass
                        else:
                            to_delete = [
                                q for q in existing_episode["telegram"]
                                if self._same_stream_release(q, quality)
                            ]
                            old_ids = [q.get("id") for q in to_delete if q.get("id")]
                            ids_to_delete.extend(old_ids)

                            if to_delete:
                                existing_episode["telegram"] = [
                                    q for q in existing_episode["telegram"]
                                    if not self._same_stream_release(q, quality)
                                ]
                                existing_episode["telegram"].append(quality)
                                changed = True
                            else:
                                existing_episode["telegram"].append(quality)
                                changed = True

            if not changed:
                return tv_id

            existing_tv["added_on"] = self._tv_first_added_on(existing_tv, incoming_added_on)
            # Latest order follows latest Telegram upload date, not /scan or /rescan time.
            existing_tv["updated_on"] = self._tv_latest_added_on(existing_tv, incoming_added_on)
            existing_tv["rev"] = current_rev + 1

            # ---------------- MOVE DB IF NEEDED ----------------
            if existing_db_index != self.current_db_index:
                try:
                    if await self._move_document("tv", existing_tv, existing_db_index):
                        await self._schedule_quality_deletes(ids_to_delete)
                        return tv_id
                except Exception as e:
                    LOGGER.error(f"Error moving TV show to {current_db_key}: {e}")
                    if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                        return await self._handle_storage_error(self.update_tv_show, tv_show_data, total_storage_dbs=total_storage_dbs)
                return tv_id

            try:
                cas_result = await self.dbs[existing_db_key]["tv"].replace_one(
                    self._cas_filter(tv_id, current_rev), existing_tv
                )
            except Exception as e:
                LOGGER.error(f"Failed to update TV show {tmdb_id} in {existing_db_key}: {e}")
                if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                    return await self._handle_storage_error(self.update_tv_show, tv_show_data, total_storage_dbs=total_storage_dbs)
                return None

            if cas_result.matched_count:
                await self._schedule_quality_deletes(ids_to_delete)
                return tv_id

            LOGGER.warning(
                f"CAS conflict updating TV show {tmdb_id} -- concurrent write detected, "
                f"retrying ({attempt + 1}/{MAX_CAS_RETRIES})"
            )

        LOGGER.error(f"Gave up updating TV show {tmdb_id} after {MAX_CAS_RETRIES} CAS retries (high write contention).")
        return None
    
    async def sort_movies(self, sort_params, page, page_size, genre_filter=None):
        sort_dict = self._get_sort_dict(sort_params)
        filter_dict = {"genres": {"$in": [genre_filter]}} if genre_filter else {}
        results, dbs_checked, total_count = await self._paginate_collection(
            "movie", sort_dict, page, page_size, filter_dict=filter_dict
        )
        total_pages = (total_count + page_size - 1) // page_size
        return {
            "total_count": total_count,
            "total_pages": total_pages,
            "databases_checked": dbs_checked,
            "current_page": page,
            "movies": [convert_objectid_to_str(result) for result in results],
        }

    async def sort_tv_shows(self, sort_params, page, page_size, genre_filter=None):
        sort_dict = self._get_sort_dict(sort_params)
        filter_dict = {"genres": {"$in": [genre_filter]}} if genre_filter else {}
        results, dbs_checked, total_count = await self._paginate_collection(
            "tv", sort_dict, page, page_size, filter_dict=filter_dict
        )
        total_pages = (total_count + page_size - 1) // page_size
        return {
            "total_count": total_count,
            "total_pages": total_pages,
            "databases_checked": dbs_checked,
            "current_page": page,
            "tv_shows": [convert_objectid_to_str(result) for result in results],
        }

    async def search_documents(
            self, 
            query: str, 
            page: int, 
            page_size: int
        ) -> dict:

            skip = (page - 1) * page_size
            
            words = query.split()
            regex_query = {
                '$regex': '.*' + '.*'.join(words) + '.*', 
                '$options': 'i'
            }
            
            tv_pipeline = [
                {"$match": {"$or": [
                    {"title": regex_query},
                    {"seasons.episodes.telegram.name": regex_query}
                ]}},
                {"$project": {
                    "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1, "imdb_id": 1,
                    "release_year": 1, "poster": 1, "backdrop": 1, "description": 1, "logo": 1,
                    "media_type": 1, "db_index": 1
                }}
            ]
            
            movie_pipeline = [
                {"$match": {"$or": [
                    {"title": regex_query},
                    {"telegram.name": regex_query}
                ]}},
                {"$project": {
                    "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1,
                    "release_year": 1, "poster": 1, "backdrop": 1, "description": 1,
                    "media_type": 1, "db_index": 1, "imdb_id": 1, "logo": 1
                }}
            ]
            
            results = []
            dbs_checked = []
            
            active_db_key = f"storage_{self.current_db_index}"
            active_db = self.dbs[active_db_key]
            dbs_checked.append(self.current_db_index)
            
            tv_results = await active_db["tv"].aggregate(tv_pipeline).to_list(None)
            movie_results = await active_db["movie"].aggregate(movie_pipeline).to_list(None)
            combined = tv_results + movie_results
            results.extend(combined)
            
            if len(results) < page_size:
                previous_db_index = self.current_db_index - 1
                while previous_db_index > 0 and len(results) < page_size:
                    prev_db_key = f"storage_{previous_db_index}"
                    prev_db = self.dbs[prev_db_key]
                    tv_results_prev = await prev_db["tv"].aggregate(tv_pipeline).to_list(None)
                    movie_results_prev = await prev_db["movie"].aggregate(movie_pipeline).to_list(None)
                    combined_prev = tv_results_prev + movie_results_prev
                    results.extend(combined_prev)
                    dbs_checked.append(previous_db_index)
                    previous_db_index -= 1

            total_count = 0
            for db_index in dbs_checked:
                key = f"storage_{db_index}"
                db = self.dbs[key]
                tv_count = await db["tv"].count_documents({
                    "$or": [
                        {"title": regex_query},
                        {"seasons.episodes.telegram.name": regex_query}
                    ]
                })
                movie_count = await db["movie"].count_documents({
                    "$or": [
                        {"title": regex_query},
                        {"telegram.name": regex_query}
                    ]
                })
                total_count += (tv_count + movie_count)
            
            paged_results = results[skip:skip + page_size]

            return {
                "total_count": total_count,
                "results": [convert_objectid_to_str(doc) for doc in paged_results]
            }


    async def get_media_details(
        self, 
        imdb_id: str,
        season_number: Optional[int] = None, 
        episode_number: Optional[int] = None
    ) -> Optional[dict]:

        for db_idx in range(self.current_db_index, 0, -1):
            db_key = f"storage_{db_idx}"
            
            if episode_number is not None and season_number is not None:
                tv_show = await self.dbs[db_key]["tv"].find_one({"imdb_id": imdb_id})
                if tv_show:
                    for season in tv_show.get("seasons", []):
                        if season.get("season_number") == season_number:
                            for episode in season.get("episodes", []):
                                if episode.get("episode_number") == episode_number:
                                    details = convert_objectid_to_str(episode)
                                    details.update({
                                        "imdb_id": imdb_id,
                                        "type": "tv",
                                        "season_number": season_number,
                                        "episode_number": episode_number,
                                        "backdrop": episode.get("episode_backdrop"),
                                        "db_index": db_idx
                                    })
                                    return details
            
            elif season_number is not None:
                tv_show = await self.dbs[db_key]["tv"].find_one({"imdb_id": imdb_id})
                if tv_show:
                    for season in tv_show.get("seasons", []):
                        if season.get("season_number") == season_number:
                            details = convert_objectid_to_str(season)
                            details.update({
                                "imdb_id": imdb_id,
                                "type": "tv",
                                "season_number": season_number,
                                "db_index": db_idx
                            })
                            return details
            
            else:
                tv_doc = await self.dbs[db_key]["tv"].find_one({"imdb_id": imdb_id})
                if tv_doc:
                    tv_doc = convert_objectid_to_str(tv_doc)
                    tv_doc["type"] = "tv"
                    tv_doc["db_index"] = db_idx
                    return tv_doc
                
                movie_doc = await self.dbs[db_key]["movie"].find_one({"imdb_id": imdb_id})
                if movie_doc:
                    movie_doc = convert_objectid_to_str(movie_doc)
                    movie_doc["type"] = "movie"
                    movie_doc["db_index"] = db_idx
                    return movie_doc
        
        return None

    # -------------------------------
    # DB Method for Edit Post
    # -------------------------------

    async def get_document(self, media_type: str, tmdb_id: int, db_index: int) -> Optional[Dict[str, Any]]:
        db_key = f"storage_{db_index}"
        if canonical_media_type(media_type) == "tv":
            collection_name = "tv"
        else:
            collection_name = "movie"
        document = await self.dbs[db_key][collection_name].find_one({"tmdb_id": int(tmdb_id)})
        return convert_objectid_to_str(document) if document else None

    async def find_document_type(self, tmdb_id: int, db_index: int) -> Optional[str]:
        """Return the collection type that currently owns a media record."""
        db_key = f"storage_{db_index}"
        storage = self.dbs.get(db_key)
        if storage is None:
            return None
        for candidate in ("movie", "tv"):
            if await storage[candidate].find_one({"tmdb_id": int(tmdb_id)}, {"_id": 1}):
                return candidate
        return None

    async def update_document(
        self, media_type: str, tmdb_id: int, db_index: int, update_data: Dict[str, Any]
    ):
        update_data.pop('_id', None)
        db_key = f"storage_{db_index}"
        if canonical_media_type(media_type) == "tv":
            collection_name = "tv"
        else:
            collection_name = "movie"
        collection = self.dbs[db_key][collection_name]

        try:
            result = await collection.update_one({"tmdb_id": int(tmdb_id)}, {"$set": update_data})

            return result.modified_count > 0

        except Exception as e:
            err_str = str(e).lower()
            LOGGER.error(f"Error updating document in {db_key}: {e}")
            if "storage" in err_str or "quota" in err_str:
                total_storage_dbs = len(self.dbs) - 1
                db_index_int = int(db_index)
                next_db_index = (db_index_int % total_storage_dbs) + 1
                if next_db_index == 1:
                    LOGGER.warning("⚠️ All storage databases are full! Add more.")
                    return False

                new_db_key = f"storage_{next_db_index}"
                LOGGER.info(f"Switching from {db_key} to {new_db_key} due to storage error.")

                try:
                    old_doc = await self.dbs[db_key][collection_name].find_one({"tmdb_id": int(tmdb_id)})
                    if not old_doc:
                        LOGGER.error(f"Document with tmdb_id {tmdb_id} not found in {db_key} during migration.")
                        return False

                    old_doc.update(update_data)
                    old_doc["db_index"] = next_db_index
                    old_doc.pop("_id", None)
                    insert_result = await self.dbs[new_db_key][collection_name].insert_one(old_doc)
                    LOGGER.info(f"Inserted document {insert_result.inserted_id} into {new_db_key}")
                    await self.dbs[db_key][collection_name].delete_one({"tmdb_id": int(tmdb_id)})
                    LOGGER.info(f"Deleted document tmdb_id {tmdb_id} from {db_key}")
                    self.current_db_index = next_db_index
                    await self.update_current_db_index()
                    LOGGER.info(f"Switched to {new_db_key} and document migrated successfully.")
                    return True

                except Exception as migrate_error:
                    LOGGER.error(f"Error migrating document tmdb_id {tmdb_id} to {new_db_key}: {migrate_error}")
                    return False
            raise

    async def delete_document(self, media_type: str, tmdb_id: int, db_index: int) -> bool:
        db_key = f"storage_{db_index}"
        media_type = canonical_media_type(media_type)

        if media_type == "movie":
            doc = await self.dbs[db_key]["movie"].find_one({"tmdb_id": tmdb_id})
            if doc and "telegram" in doc:
                for quality in doc["telegram"]:
                    try:
                        old_id = quality.get("id")
                        if old_id:
                            await self._schedule_stream_delete(old_id)
                    except Exception as e:
                        LOGGER.error(f"Failed to queue file for deletion: {e}")
            
            result = await self.dbs[db_key]["movie"].delete_one({"tmdb_id": tmdb_id})
        else:
            doc = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
            if doc and "seasons" in doc:
                for season in doc["seasons"]:
                    for episode in season.get("episodes", []):
                        for quality in episode.get("telegram", []):
                            try:
                                old_id = quality.get("id")
                                if old_id:
                                    await self._schedule_stream_delete(old_id)
                            except Exception as e:
                                LOGGER.error(f"Failed to queue file for deletion: {e}")
            
            result = await self.dbs[db_key]["tv"].delete_one({"tmdb_id": tmdb_id})
        
        if result.deleted_count > 0:
            LOGGER.info(f"{media_type} with tmdb_id {tmdb_id} deleted successfully.")
            return True
        LOGGER.info(f"No document found with tmdb_id {tmdb_id}.")
        return False

    async def get_title_by_stream_id(self, stream_id_hash: str) -> Optional[str]:
        """Look up the original media title across all storage DBs using the telegram file ID hash.
        For TV shows, it includes the Season and Episode number in the title."""
        for i in range(1, self.current_db_index + 1):
            db = self.dbs[f"storage_{i}"]
            
            # Check Movies
            movie = await db["movie"].find_one({"telegram.id": stream_id_hash})
            if movie and "telegram" in movie:
                for t in movie["telegram"]:
                    if t.get("id") == stream_id_hash:
                        return movie.get("title")

            # Check TV Shows
            tv = await db["tv"].find_one({"seasons.episodes.telegram.id": stream_id_hash})
            if tv and "seasons" in tv:
                title = tv.get("title", "Unknown Series")
                for season in tv.get("seasons", []):
                    for episode in season.get("episodes", []):
                        for t in episode.get("telegram", []):
                            if t.get("id") == stream_id_hash:
                                s_num = season.get("season_number", 0)
                                e_num = episode.get("episode_number", 0)
                                return f"{title} S{s_num:02d}E{e_num:02d}"

        return None

    async def delete_media_by_stream_id(self, stream_id_hash: str) -> bool:
        """Finds and removes a specific stream quality by its hash across all DBs. 
        If it's the last quality, it cleans up the movie or episode/season/show."""
        for i in range(1, self.current_db_index + 1):
            db = self.dbs[f"storage_{i}"]
            
            # Check Movies
            movie = await db["movie"].find_one({"telegram.id": stream_id_hash})
            if movie:
                movie["telegram"] = [q for q in movie.get("telegram", []) if q.get("id") != stream_id_hash]
                if len(movie["telegram"]) == 0:
                    await db["movie"].delete_one({"_id": movie["_id"]})
                else:
                    movie['updated_on'] = self._movie_latest_added_on(movie, movie.get('updated_on'))
                    await db["movie"].replace_one({"_id": movie["_id"]}, movie)
                return True

            # Check TV Shows
            tv = await db["tv"].find_one({"seasons.episodes.telegram.id": stream_id_hash})
            if tv:
                for season in tv.get("seasons", []):
                    for episode in season.get("episodes", []):
                        for q in episode.get("telegram", []):
                            if q.get("id") == stream_id_hash:
                                episode["telegram"] = [t for t in episode.get("telegram", []) if t.get("id") != stream_id_hash]
                                if len(episode["telegram"]) == 0:
                                    season["episodes"] = [e for e in season.get("episodes", []) if e.get("episode_number") != episode.get("episode_number")]
                                    if len(season["episodes"]) == 0:
                                        tv["seasons"] = [s for s in tv.get("seasons", []) if s.get("season_number") != season.get("season_number")]
                                        if len(tv["seasons"]) == 0:
                                            await db["tv"].delete_one({"_id": tv["_id"]})
                                            return True
                                tv['updated_on'] = self._tv_latest_added_on(tv, tv.get('updated_on'))
                                await db["tv"].replace_one({"_id": tv["_id"]}, tv)
                                return True
        return False

    async def delete_movie_quality(self, tmdb_id: int, db_index: int, id: str) -> bool:
        db_key = f"storage_{db_index}"
        movie = await self.dbs[db_key]["movie"].find_one({"tmdb_id": tmdb_id})
        
        if not movie or "telegram" not in movie:
            return False

        for q in movie["telegram"]:
            if q.get("id") == id:
                try:
                    old_id = q.get("id")
                    if old_id:
                        await self._schedule_stream_delete(old_id)
                except Exception as e:
                    LOGGER.error(f"Failed to queue file for deletion: {e}")
                break
        
        original_len = len(movie["telegram"])
        movie["telegram"] = [q for q in movie["telegram"] if q.get("id") != id]
        
        if len(movie["telegram"]) == original_len:
            return False
        
        movie['updated_on'] = self._movie_latest_added_on(movie, movie.get('updated_on'))
        result = await self.dbs[db_key]["movie"].replace_one({"tmdb_id": tmdb_id}, movie)
        return result.modified_count > 0

    async def delete_tv_episode(self, tmdb_id: int, db_index: int, season_number: int, episode_number: int) -> bool:
        db_key = f"storage_{db_index}"
        tv = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
        
        if not tv or "seasons" not in tv:
            return False
        
        found = False
        for season in tv["seasons"]:
            if season.get("season_number") == season_number:
                for ep in season["episodes"]:
                    if ep.get("episode_number") == episode_number:
                        for quality in ep.get("telegram", []):
                            try:
                                old_id = quality.get("id")
                                if old_id:
                                    await self._schedule_stream_delete(old_id)
                            except Exception as e:
                                LOGGER.error(f"Failed to queue file for deletion: {e}")
                        break
                
                original_len = len(season["episodes"])
                season["episodes"] = [ep for ep in season["episodes"] if ep.get("episode_number") != episode_number]
                found = original_len > len(season["episodes"])
                break
        
        if not found:
            return False
        
        tv['updated_on'] = self._tv_latest_added_on(tv, tv.get('updated_on'))
        result = await self.dbs[db_key]["tv"].replace_one({"tmdb_id": tmdb_id}, tv)
        return result.modified_count > 0

    async def delete_tv_season(self, tmdb_id: int, db_index: int, season_number: int) -> bool:
        db_key = f"storage_{db_index}"
        tv = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
        
        if not tv or "seasons" not in tv:
            return False
        
        for season in tv["seasons"]:
            if season.get("season_number") == season_number:
                for episode in season.get("episodes", []):
                    for quality in episode.get("telegram", []):
                        try:
                            old_id = quality.get("id")
                            if old_id:
                                await self._schedule_stream_delete(old_id)
                        except Exception as e:
                            LOGGER.error(f"Failed to queue file for deletion: {e}")
                break
        
        original_len = len(tv["seasons"])
        tv["seasons"] = [s for s in tv["seasons"] if s.get("season_number") != season_number]
        
        if len(tv["seasons"]) == original_len:
            return False
        
        tv['updated_on'] = self._tv_latest_added_on(tv, tv.get('updated_on'))
        result = await self.dbs[db_key]["tv"].replace_one({"tmdb_id": tmdb_id}, tv)
        return result.modified_count > 0

    async def delete_tv_quality(self, tmdb_id: int, db_index: int, season_number: int, episode_number: int, id: str) -> bool:
        db_key = f"storage_{db_index}"
        tv = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
        
        if not tv or "seasons" not in tv:
            return False
        
        found = False
        for season in tv["seasons"]:
            if season.get("season_number") == season_number:
                for episode in season["episodes"]:
                    if episode.get("episode_number") == episode_number and "telegram" in episode:
                        for q in episode["telegram"]:
                            if q.get("id") == id:
                                try:
                                    old_id = q.get("id")
                                    if old_id:
                                        await self._schedule_stream_delete(old_id)
                                except Exception as e:
                                    LOGGER.error(f"Failed to queue file for deletion: {e}")
                                break
                        
                        original_len = len(episode["telegram"])
                        episode["telegram"] = [q for q in episode["telegram"] if q.get("id") != id]
                        found = original_len > len(episode["telegram"])
                        break
        
        if not found:
            return False
        tv['updated_on'] = self._tv_latest_added_on(tv, tv.get('updated_on'))
        result = await self.dbs[db_key]["tv"].replace_one({"tmdb_id": tmdb_id}, tv)
        return result.modified_count > 0


    # Get per-DB statistics (movies, tv shows, used size, etc.)
    async def get_database_stats(self):
        if self.disabled:
            return []
        stats = []
        for key in self.dbs.keys():
            if key.startswith("storage_"):
                db = self.dbs[key]
                movie_count = await db["movie"].count_documents({})
                tv_count = await db["tv"].count_documents({})
                db_stats = await db.command("dbstats")
                stats.append({
                    "db_name": key,
                    "movie_count": movie_count,
                    "tv_count": tv_count,
                    "storageSize": db_stats.get("storageSize", 0),
                    "dataSize": db_stats.get("dataSize", 0)
                })
        return stats



    # -------------------------------
    # API Token Methods
    # -------------------------------

    async def add_api_token(self, name: str, daily_limit_gb: float = None, monthly_limit_gb: float = None, user_id: int = None) -> dict:
        # If a user_id is provided, return existing token if already created
        if user_id:
            existing = await self.dbs["tracking"]["api_tokens"].find_one({"user_id": user_id})
            if existing:
                return convert_objectid_to_str(existing)

        alphabet = string.ascii_letters + string.digits
        token = ''.join(secrets.choice(alphabet) for _ in range(32))
        
        token_doc = {
            "name": name,
            "token": token,
            "user_id": user_id,
            "created_at": datetime.utcnow(),
            "limits": {
                "daily_limit_gb": daily_limit_gb if daily_limit_gb else 0,
                "monthly_limit_gb": monthly_limit_gb if monthly_limit_gb else 0
            },
            "usage": {
                "total_bytes": 0,
                "daily": {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "bytes": 0},
                "monthly": {"month": datetime.now(timezone.utc).strftime("%Y-%m"), "bytes": 0}
            }
        }
        
        await self.dbs["tracking"]["api_tokens"].insert_one(token_doc)
        return convert_objectid_to_str(token_doc)

    async def get_api_token(self, token: str) -> Optional[dict]:
        doc = await self.dbs["tracking"]["api_tokens"].find_one({"token": token})
        return convert_objectid_to_str(doc) if doc else None

    async def get_all_api_tokens(self) -> List[dict]:
        cursor = self.dbs["tracking"]["api_tokens"].find().sort("created_at", DESCENDING)
        tokens = await cursor.to_list(None)
        return [convert_objectid_to_str(token) for token in tokens]

    async def revoke_api_token(self, token: str) -> bool:
        result = await self.dbs["tracking"]["api_tokens"].delete_one({"token": token})
        return result.deleted_count > 0

    async def link_token_user(self, token: str, user_id: int) -> bool:
        """Link an existing token to a Telegram user_id."""
        result = await self.dbs["tracking"]["api_tokens"].update_one(
            {"token": token},
            {"$set": {"user_id": user_id}}
        )
        return result.modified_count > 0

    async def update_token_usage(self, token: str, bytes_delta: int):
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month_str = datetime.now(timezone.utc).strftime("%Y-%m")
        
        token_doc = await self.dbs["tracking"]["api_tokens"].find_one({"token": token})
        if not token_doc:
             return

        current_daily = token_doc.get("usage", {}).get("daily", {})
        if current_daily.get("date") != today_str:
            await self.dbs["tracking"]["api_tokens"].update_one(
                {"token": token},
                {"$set": {"usage.daily": {"date": today_str, "bytes": 0}}}
            )

        current_monthly = token_doc.get("usage", {}).get("monthly", {})
        if current_monthly.get("month") != month_str:
            await self.dbs["tracking"]["api_tokens"].update_one(
                {"token": token},
                {"$set": {"usage.monthly": {"month": month_str, "bytes": 0}}}
            )

        await self.dbs["tracking"]["api_tokens"].update_one(
            {"token": token},
            {
                "$inc": {
                    "usage.total_bytes": bytes_delta,
                    "usage.daily.bytes": bytes_delta,
                    "usage.monthly.bytes": bytes_delta
                }
            }
        )

    async def update_api_token_limits(self, token: str, daily_limit_gb: float, monthly_limit_gb: float) -> bool:
        result = await self.dbs["tracking"]["api_tokens"].update_one(
            {"token": token},
            {"$set": {
                "limits": {
                    "daily_limit_gb": daily_limit_gb if daily_limit_gb else 0,
                    "monthly_limit_gb": monthly_limit_gb if monthly_limit_gb else 0
                }
            }}
        )
        return result.modified_count > 0

    # -------------------------------
    # Admin / Link Checker Methods
    # -------------------------------
    async def flag_dead_link(self, media_type: str, tmdb_id: int, db_index: int, quality_id: str) -> bool:
        """
        Flags a specific telegram quality entry as 'is_dead: True'.
        """
        media_type = canonical_media_type(media_type)
        db_key = f"storage_{db_index}"
        
        if media_type == "movie":
            # Direct update in the telegram array for movies
            result = await self.dbs[db_key]["movie"].update_one(
                {"tmdb_id": tmdb_id, "telegram.id": quality_id},
                {"$set": {"telegram.$.is_dead": True}}
            )
            return result.modified_count > 0
            
        elif media_type == "tv":
            # Nested update for TV (arrayFilters needed since we don't know the exact indices)
            # Find the TV show docs
            tv = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
            if not tv or "seasons" not in tv:
                return False
                
            found = False
            for s_idx, season in enumerate(tv["seasons"]):
                for e_idx, episode in enumerate(season.get("episodes", [])):
                    for q_idx, quality in enumerate(episode.get("telegram", [])):
                        if quality.get("id") == quality_id:
                            tv["seasons"][s_idx]["episodes"][e_idx]["telegram"][q_idx]["is_dead"] = True
                            found = True
                            break
                    if found: break
                if found: break
                
            if found:
                tv["updated_on"] = self._tv_latest_added_on(tv, tv.get("updated_on"))
                result = await self.dbs[db_key]["tv"].replace_one({"tmdb_id": tmdb_id}, tv)
                return result.modified_count > 0
                
        return False

    async def get_all_dead_links(self) -> List[dict]:
        """
        Scans all active storage databases for both movies and TV shows, returning a
        flattened list of dead links with their metadata for the Admin UI.
        """
        dead_links = []
        
        for i in range(1, self.current_db_index + 1):
            db_key = f"storage_{i}"
            db = self.dbs[db_key]
            
            # --- Scan Movies ---
            # Match any movie where at least one telegram entry has is_dead=True
            movie_cursor = db["movie"].find({"telegram.is_dead": True})
            async for movie in movie_cursor:
                for quality in movie.get("telegram", []):
                    if quality.get("is_dead"):
                        dead_links.append({
                            "type": "movie",
                            "tmdb_id": movie.get("tmdb_id"),
                            "db_index": movie.get("db_index", i),
                            "title": movie.get("title"),
                            "year": movie.get("year"),
                            "poster": movie.get("poster"),
                            "quality_id": quality.get("id"),
                            "quality": quality.get("quality"),
                            "size": quality.get("size"),
                            "date_added": quality.get("date_added")
                        })
                        
            # --- Scan TV Shows ---
            # Match any TV where seasons.episodes.telegram.is_dead=True
            tv_cursor = db["tv"].find({"seasons.episodes.telegram.is_dead": True})
            async for tv in tv_cursor:
                title = tv.get("title")
                year = tv.get("year")
                poster = tv.get("poster")
                for season in tv.get("seasons", []):
                    s_num = season.get("season_number")
                    for ep in season.get("episodes", []):
                        e_num = ep.get("episode_number")
                        for quality in ep.get("telegram", []):
                            if quality.get("is_dead"):
                                dead_links.append({
                                    "type": "tv",
                                    "tmdb_id": tv.get("tmdb_id"),
                                    "db_index": tv.get("db_index", i),
                                    "title": f"{title} (S{s_num:02d}E{e_num:02d})",
                                    "year": year,
                                    "poster": poster,
                                    "season": s_num,
                                    "episode": e_num,
                                    "quality_id": quality.get("id"),
                                    "quality": quality.get("quality"),
                                    "size": quality.get("size"),
                                    "date_added": quality.get("date_added")
                                })
                                
        return dead_links

    # -------------------------------
    # Stream Analytics
    # -------------------------------

    async def log_stream_stats(self, stats: dict) -> None:
        """Persist a finished-stream record to the tracking DB for analytics."""
        try:
            record = {
                "stream_id":   stats.get("stream_id"),
                "msg_id":      stats.get("msg_id"),
                "chat_id":     stats.get("chat_id"),
                "dc_id":       stats.get("dc_id"),
                "title":       stats.get("meta", {}).get("title"),  # Added title
                "client_index": stats.get("client_index"),
                "total_bytes": stats.get("total_bytes", 0),
                "duration_sec": round(stats.get("duration", 0.0), 2),
                "avg_mbps":    round(stats.get("avg_mbps", 0.0), 3),
                "peak_mbps":   round(stats.get("peak_mbps", 0.0), 3),
                "status":      stats.get("status", "finished"),
                "parallelism": stats.get("parallelism"),
                "chunk_size":  stats.get("chunk_size"),
                "logged_at":   datetime.utcnow(),
            }
            await self.dbs["tracking"]["stream_analytics"].insert_one(record)
        except Exception as e:
            LOGGER.warning(f"Stream analytics log failed: {e}")

    async def get_stream_analytics(self, limit: int = 200) -> dict:
        """Return summary stats + recent stream records from the tracking DB."""
        try:
            col = self.dbs["tracking"]["stream_analytics"]

            # Aggregate totals
            pipeline = [
                {"$group": {
                    "_id": None,
                    "total_streams":     {"$sum": 1},
                    "total_bytes":       {"$sum": "$total_bytes"},
                    "avg_speed":         {"$avg": "$avg_mbps"},
                    "peak_speed":        {"$max": "$peak_mbps"},
                    "avg_duration":      {"$avg": "$duration_sec"},
                }},
            ]
            agg = await col.aggregate(pipeline).to_list(1)
            summary = agg[0] if agg else {}
            summary.pop("_id", None)

            # Per-client breakdown
            per_client_pipeline = [
                {"$group": {
                    "_id":          "$client_index",
                    "streams":      {"$sum": 1},
                    "avg_mbps":     {"$avg": "$avg_mbps"},
                    "peak_mbps":    {"$max": "$peak_mbps"},
                    "total_bytes":  {"$sum": "$total_bytes"},
                }},
                {"$sort": {"_id": 1}},
            ]
            per_client = await col.aggregate(per_client_pipeline).to_list(None)
            for row in per_client:
                row["client_index"] = row.pop("_id")
                row["avg_mbps"]     = round(row.get("avg_mbps", 0), 3)
                row["peak_mbps"]    = round(row.get("peak_mbps", 0), 3)

            # Recent records (newest first)
            recent_cursor = col.find(
                {},
                {"_id": 0, "stream_id": 1, "client_index": 1, "dc_id": 1,
                 "total_bytes": 1, "duration_sec": 1, "avg_mbps": 1,
                 "peak_mbps": 1, "status": 1, "logged_at": 1, "title": 1}
            ).sort("logged_at", DESCENDING).limit(limit)
            recent = await recent_cursor.to_list(None)
            for r in recent:
                if "logged_at" in r:
                    r["logged_at"] = r["logged_at"].isoformat()

            return {
                "summary":    summary,
                "per_client": per_client,
                "recent":     recent,
            }
        except Exception as e:
            LOGGER.error(f"get_stream_analytics error: {e}")
            return {"summary": {}, "per_client": [], "recent": []}



    async def replace_media_metadata(
        self,
        media_type: str,
        tmdb_id: int,
        db_index: int,
        metadata: Dict[str, Any]
    ) -> Optional[dict]:
        db_key = f"storage_{db_index}"
        collection_name = "tv" if canonical_media_type(media_type) == "tv" else "movie"
        collection = self.dbs[db_key][collection_name]

        current_doc = await collection.find_one({"tmdb_id": int(tmdb_id)})
        if not current_doc:
            return None

        current_doc.pop("_id", None)

        if collection_name == "movie":
            preserved_telegram = current_doc.get("telegram", [])
            current_doc.update({
                "tmdb_id": int(metadata.get("tmdb_id") or tmdb_id),
                "imdb_id": metadata.get("imdb_id"),
                "title": metadata.get("title") or current_doc.get("title"),
                "release_year": metadata.get("release_year", current_doc.get("release_year")),
                "rating": metadata.get("rating", current_doc.get("rating")),
                "description": metadata.get("description", current_doc.get("description")),
                "poster": metadata.get("poster", current_doc.get("poster")),
                "backdrop": metadata.get("backdrop", current_doc.get("backdrop")),
                "logo": metadata.get("logo", current_doc.get("logo")),
                "genres": metadata.get("genres", current_doc.get("genres", [])),
                "cast": metadata.get("cast", current_doc.get("cast", [])),
                "runtime": metadata.get("runtime", current_doc.get("runtime")),
                "media_type": "movie",
                "telegram": preserved_telegram,
                "updated_on": current_doc.get("updated_on"),
            })
        else:
            preserved_seasons = current_doc.get("seasons", [])
            current_doc.update({
                "tmdb_id": int(metadata.get("tmdb_id") or tmdb_id) if metadata.get("tmdb_id") else int(tmdb_id),
                "imdb_id": metadata.get("imdb_id"),
                "title": metadata.get("title") or current_doc.get("title"),
                "release_year": metadata.get("release_year", current_doc.get("release_year")),
                "rating": metadata.get("rating", current_doc.get("rating")),
                "description": metadata.get("description", current_doc.get("description")),
                "poster": metadata.get("poster", current_doc.get("poster")),
                "backdrop": metadata.get("backdrop", current_doc.get("backdrop")),
                "logo": metadata.get("logo", current_doc.get("logo")),
                "genres": metadata.get("genres", current_doc.get("genres", [])),
                "cast": metadata.get("cast", current_doc.get("cast", [])),
                "runtime": metadata.get("runtime", current_doc.get("runtime")),
                "media_type": "tv",
                "seasons": preserved_seasons,
                "updated_on": current_doc.get("updated_on"),
            })

        new_tmdb_id = int(current_doc["tmdb_id"])
        await collection.delete_one({"tmdb_id": int(tmdb_id)})
        await collection.insert_one(current_doc)

        updated_doc = await collection.find_one({"tmdb_id": new_tmdb_id})
        return convert_objectid_to_str(updated_doc) if updated_doc else None

    # ─────────────────────────────────────────────
    # Subtitle Methods
    # ─────────────────────────────────────────────

    async def insert_subtitle(
        self,
        imdb_id: str,
        subtitle_id: str,
        language: str,
        name: str,
        fmt: str,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        source_type: str = "telegram",
        source_chat_id: Optional[int] = None,
        source_topic_id: Optional[int] = None,
        source_link: Optional[str] = None,
        date_added: Optional[datetime] = None,
    ) -> bool:
        """
        Attach a subtitle entry to a movie or TV episode.

        This used to be a find_one() -> mutate the whole document in
        Python -> replace_one() cycle. That's what produced the lost-update
        race with insert_media()/update_tv_show(): both handlers read a
        full copy of the same TV document, and whichever replace_one()
        landed last won, blindly overwriting whatever the other had just
        written (including a subtitle that had already been saved).

        This version never reads the document at all. It updates only the
        `subtitles` sub-array in place using $set (overwrite an existing
        language entry) or $push (add a new one), via arrayFilters to
        reach the right season/episode. Because the write is scoped to
        that one array path, it can't touch -- or be undone by -- a
        concurrent write to a different field (e.g. `telegram`).

        Returns True if the media was found and updated.
        """
        subtitle_doc = {
            "id": subtitle_id,
            "language": language,
            "name": name,
            "format": fmt,
            "source_type": source_type,
        }
        dt_added = self._coerce_datetime(date_added)
        if dt_added is not None:
            subtitle_doc["date_added"] = dt_added
        if source_chat_id is not None:
            subtitle_doc["source_chat_id"] = source_chat_id
        if source_topic_id is not None:
            subtitle_doc["source_topic_id"] = source_topic_id
        if source_link:
            subtitle_doc["source_link"] = source_link

        for db_idx in range(self.current_db_index, 0, -1):
            db_key = f"storage_{db_idx}"
            db = self.dbs[db_key]

            if season_number is not None and episode_number is not None:
                # ---- TV episode ----
                array_filters = [
                    {"s.season_number": season_number},
                    {"e.episode_number": episode_number},
                ]

                # 1) Same Telegram subtitle message already exists on this exact
                #    episode -> overwrite it in place.
                #    Do NOT replace by language only: a movie/episode can have
                #    multiple Sinhala/English subtitles for different releases.
                set_result = await db["tv"].update_one(
                    {
                        "imdb_id": imdb_id,
                        "seasons": {
                            "$elemMatch": {
                                "season_number": season_number,
                                "episodes": {
                                    "$elemMatch": {
                                        "episode_number": episode_number,
                                        "subtitles.id": subtitle_id,
                                    }
                                },
                            }
                        },
                    },
                    {
                        "$set": {"seasons.$[s].episodes.$[e].subtitles.$[sub]": subtitle_doc},
                        "$inc": {"rev": 1},
                    },
                    array_filters=array_filters + [{"sub.id": subtitle_id}],
                )
                if set_result.matched_count:
                    return True

                # 2) Same target + same language + same filename was re-sent with
                #    a new Telegram message id -> replace the old subtitle and
                #    delete the old Telegram message automatically. Different
                #    filenames are kept so exact release choices still work.
                existing_tv = await db["tv"].find_one({"imdb_id": imdb_id})
                if existing_tv:
                    for s_doc in existing_tv.get("seasons", []):
                        if s_doc.get("season_number") != season_number:
                            continue
                        for e_doc in s_doc.get("episodes", []):
                            if e_doc.get("episode_number") != episode_number:
                                continue
                            for old_sub in e_doc.get("subtitles", []) or []:
                                if (
                                    old_sub.get("id") != subtitle_id
                                    and old_sub.get("language") == language
                                    and old_sub.get("format") == fmt
                                    and self._dedupe_name_key(old_sub.get("name")) == self._dedupe_name_key(name)
                                ):
                                    old_id = old_sub.get("id")
                                    set_result = await db["tv"].update_one(
                                        {
                                            "imdb_id": imdb_id,
                                            "seasons": {
                                                "$elemMatch": {
                                                    "season_number": season_number,
                                                    "episodes": {
                                                        "$elemMatch": {
                                                            "episode_number": episode_number,
                                                            "subtitles.id": old_id,
                                                        }
                                                    },
                                                }
                                            },
                                        },
                                        {
                                            "$set": {"seasons.$[s].episodes.$[e].subtitles.$[sub]": subtitle_doc},
                                            "$inc": {"rev": 1},
                                        },
                                        array_filters=array_filters + [{"sub.id": old_id}],
                                    )
                                    if set_result.matched_count:
                                        await self._schedule_stream_delete(old_id)
                                        return True

                # 3) No existing matching subtitle -> append a new one.
                #    Same-language subtitles with different filenames are allowed
                #    so Stremio can show exact release/file-name choices.
                push_result = await db["tv"].update_one(
                    {
                        "imdb_id": imdb_id,
                        "seasons": {
                            "$elemMatch": {
                                "season_number": season_number,
                                "episodes.episode_number": episode_number,
                            }
                        },
                    },
                    {
                        "$push": {"seasons.$[s].episodes.$[e].subtitles": subtitle_doc},
                        "$inc": {"rev": 1},
                    },
                    array_filters=array_filters,
                )
                if push_result.matched_count:
                    return True
                # Neither matched in this shard -> try the next one.

            else:
                # ---- Movie ----
                # Replace only the same Telegram subtitle message. Allow multiple
                # subtitles in the same language for exact release/file matching.
                set_result = await db["movie"].update_one(
                    {"imdb_id": imdb_id, "subtitles.id": subtitle_id},
                    {
                        "$set": {"subtitles.$[sub]": subtitle_doc},
                        "$inc": {"rev": 1},
                    },
                    array_filters=[{"sub.id": subtitle_id}],
                )
                if set_result.matched_count:
                    return True

                # Same movie + same language + same filename was re-sent with
                # a new Telegram message id -> replace and delete old message.
                existing_movie = await db["movie"].find_one({"imdb_id": imdb_id})
                if existing_movie:
                    for old_sub in existing_movie.get("subtitles", []) or []:
                        if (
                            old_sub.get("id") != subtitle_id
                            and old_sub.get("language") == language
                            and old_sub.get("format") == fmt
                            and self._dedupe_name_key(old_sub.get("name")) == self._dedupe_name_key(name)
                        ):
                            old_id = old_sub.get("id")
                            replace_result = await db["movie"].update_one(
                                {"imdb_id": imdb_id, "subtitles.id": old_id},
                                {
                                    "$set": {"subtitles.$[sub]": subtitle_doc},
                                    "$inc": {"rev": 1},
                                },
                                array_filters=[{"sub.id": old_id}],
                            )
                            if replace_result.matched_count:
                                await self._schedule_stream_delete(old_id)
                                return True

                push_result = await db["movie"].update_one(
                    {"imdb_id": imdb_id},
                    {
                        "$push": {"subtitles": subtitle_doc},
                        "$inc": {"rev": 1},
                    },
                )
                if push_result.matched_count:
                    return True

        return False

    async def get_subtitles(
        self,
        imdb_id: str,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
    ) -> list:
        """Return the list of subtitle docs for a movie or TV episode."""
        for db_idx in range(self.current_db_index, 0, -1):
            db_key = f"storage_{db_idx}"
            db = self.dbs[db_key]

            if season_number is not None and episode_number is not None:
                tv = await db["tv"].find_one({"imdb_id": imdb_id})
                if not tv:
                    continue
                for season in tv.get("seasons", []):
                    if season.get("season_number") != season_number:
                        continue
                    for episode in season.get("episodes", []):
                        if episode.get("episode_number") != episode_number:
                            continue
                        return episode.get("subtitles", [])
            else:
                movie = await db["movie"].find_one({"imdb_id": imdb_id})
                if movie:
                    return movie.get("subtitles", [])

        return []

    async def get_subtitle_by_id(self, subtitle_id: str) -> Optional[dict]:
        """Look up subtitle doc across all DBs by its encoded hash id."""
        for db_idx in range(self.current_db_index, 0, -1):
            db_key = f"storage_{db_idx}"
            db = self.dbs[db_key]

            movie = await db["movie"].find_one({"subtitles.id": subtitle_id})
            if movie:
                for s in movie.get("subtitles", []):
                    if s.get("id") == subtitle_id:
                        return s
            tv = await db["tv"].find_one({"seasons.episodes.subtitles.id": subtitle_id})
            if tv:
                for season in tv.get("seasons", []):
                    for episode in season.get("episodes", []):
                        for s in episode.get("subtitles", []):
                            if s.get("id") == subtitle_id:
                                return s
        return None

    async def get_all_subtitles_overview(
        self,
        page: int = 1,
        page_size: int = 50,
        search: str = "",
    ) -> dict:
        """Return one bounded All Subtitles page plus summary metadata.

        Search is deliberately applied to the complete subtitle library before
        pagination. This lets the admin find an older subtitle that is not on
        the currently visible 50-row page, while keeping each mobile response
        lightweight.
        """
        try:
            page = max(1, int(page or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = max(1, min(50, int(page_size or 50)))
        except (TypeError, ValueError):
            page_size = 50

        search_query = re.sub(r"\s+", " ", str(search or "").strip().lower())
        compact_query = re.sub(r"[^a-z0-9]+", "", search_query)
        language_names = {
            "en": "english", "si": "sinhala", "ta": "tamil", "hi": "hindi",
            "fr": "french", "de": "german", "es": "spanish", "ja": "japanese",
            "ko": "korean", "zh": "chinese", "ar": "arabic", "pt": "portuguese",
            "ru": "russian", "it": "italian", "nl": "dutch", "tr": "turkish",
            "te": "telugu", "ml": "malayalam", "kn": "kannada",
        }

        results = []
        for db_idx in range(self.current_db_index, 0, -1):
            db_key = f"storage_{db_idx}"
            _db = self.dbs[db_key]

            # Movies with subtitles
            async for movie in _db["movie"].find(
                {"subtitles": {"$exists": True, "$not": {"$size": 0}}},
                {"title": 1, "imdb_id": 1, "poster": 1, "release_year": 1, "subtitles": 1}
            ):
                for sub in movie.get("subtitles", []):
                    results.append({
                        "media_type":   "movie",
                        "title":        movie.get("title", ""),
                        "media_title":  movie.get("title", ""),
                        "media_poster": movie.get("poster", ""),
                        "imdb_id":      movie.get("imdb_id", ""),
                        "poster":       movie.get("poster", ""),
                        "release_year": movie.get("release_year", ""),
                        "season":       None,
                        "episode":      None,
                        **sub,
                    })

            # TV shows with subtitles
            async for tv in _db["tv"].find(
                {"seasons.episodes.subtitles": {"$exists": True}},
                {"title": 1, "imdb_id": 1, "poster": 1, "release_year": 1, "seasons": 1}
            ):
                for season in tv.get("seasons", []):
                    for episode in season.get("episodes", []):
                        for sub in episode.get("subtitles", []):
                            results.append({
                                "media_type":   "tv",
                                "title":        tv.get("title", ""),
                                "media_title":  tv.get("title", ""),
                                "media_poster": tv.get("poster", ""),
                                "imdb_id":      tv.get("imdb_id", ""),
                                "poster":       tv.get("poster", ""),
                                "release_year": tv.get("release_year", ""),
                                "season":       season.get("season_number"),
                                "episode":      episode.get("episode_number"),
                                **sub,
                            })

        library_total = len(results)

        if search_query:
            def _matches_search(subtitle: dict) -> bool:
                language = str(subtitle.get("language") or "en").lower()
                season = subtitle.get("season")
                episode = subtitle.get("episode")
                episode_tags = []
                try:
                    if season is not None and episode is not None:
                        season_num = int(season)
                        episode_num = int(episode)
                        episode_tags.extend([
                            f"s{season_num:02d}e{episode_num:02d}",
                            f"season {season_num}",
                            f"episode {episode_num}",
                            f"{season_num}x{episode_num:02d}",
                        ])
                except (TypeError, ValueError):
                    pass

                searchable = " ".join(str(part or "") for part in [
                    subtitle.get("media_title"),
                    subtitle.get("title"),
                    subtitle.get("imdb_id"),
                    subtitle.get("name"),
                    subtitle.get("format"),
                    subtitle.get("source_type"),
                    subtitle.get("media_type"),
                    language,
                    language_names.get(language, ""),
                    *episode_tags,
                ]).lower()
                compact = re.sub(r"[^a-z0-9]+", "", searchable)
                return search_query in searchable or (compact_query and compact_query in compact)

            results = [subtitle for subtitle in results if _matches_search(subtitle)]

        total = len(results)
        language_counts = {}
        for subtitle in results:
            language = str(subtitle.get("language") or "en").lower()
            language_counts[language] = language_counts.get(language, 0) + 1

        total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        page = min(page, total_pages)
        start_index = (page - 1) * page_size
        page_items = results[start_index:start_index + page_size]

        return {
            "subtitles": page_items,
            "total": total,
            "library_total": library_total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "search": search_query,
            "language_counts": language_counts,
        }



# ─────────────────────────────────────────────────────────────
# Extra admin analytics / quota helpers (added as monkey-patched methods)
# ─────────────────────────────────────────────────────────────

def _tgst_format_bytes(num: int | float | None) -> str:
    try:
        size = float(num or 0)
    except Exception:
        size = 0.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024


def _tgst_parse_size_to_bytes(size_val) -> int:
    if isinstance(size_val, (int, float)):
        return int(size_val)
    text = str(size_val or "").strip().lower().replace(",", "")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(tb|gb|mb|kb|b)", text)
    if not m:
        return 0
    value = float(m.group(1))
    unit = m.group(2)
    mult = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}.get(unit, 1)
    return int(value * mult)


async def _database_reset_token_usage(self, token: str) -> bool:
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    month_str = datetime.utcnow().strftime("%Y-%m")
    res = await self.dbs["tracking"]["api_tokens"].update_one(
        {"token": token},
        {"$set": {
            "usage.total_bytes": 0,
            "usage.daily": {"date": today_str, "bytes": 0},
            "usage.monthly": {"month": month_str, "bytes": 0},
        }}
    )
    return bool(res.matched_count)


async def _database_get_token_usage_summary(self) -> dict:
    tokens = await self.get_all_api_tokens()
    rows = []
    total_daily = 0
    total_monthly = 0
    total_all = 0
    for t in tokens:
        usage = t.get("usage") or {}
        daily = int((usage.get("daily") or {}).get("bytes") or 0)
        monthly = int((usage.get("monthly") or {}).get("bytes") or 0)
        total = int(usage.get("total_bytes") or 0)
        total_daily += daily
        total_monthly += monthly
        total_all += total
        rows.append({
            "token": t.get("token"),
            "name": t.get("name") or "Token",
            "user_id": t.get("user_id"),
            "daily_bytes": daily,
            "monthly_bytes": monthly,
            "total_bytes": total,
            "daily_readable": _tgst_format_bytes(daily),
            "monthly_readable": _tgst_format_bytes(monthly),
            "total_readable": _tgst_format_bytes(total),
            "daily_limit_gb": (t.get("limits") or {}).get("daily_limit_gb", t.get("daily_limit_gb", 0)),
            "monthly_limit_gb": (t.get("limits") or {}).get("monthly_limit_gb", t.get("monthly_limit_gb", 0)),
            "addon_url": f"{Telegram.BASE_URL}/stremio/{t.get('token')}/manifest.json" if t.get("token") else None,
        })
    rows.sort(key=lambda r: r.get("monthly_bytes", 0), reverse=True)
    return {
        "summary": {
            "daily_bytes": total_daily, "monthly_bytes": total_monthly, "total_bytes": total_all,
            "daily_readable": _tgst_format_bytes(total_daily),
            "monthly_readable": _tgst_format_bytes(total_monthly),
            "total_readable": _tgst_format_bytes(total_all),
            "tokens": len(rows),
        },
        "tokens": rows,
    }


async def _database_get_storage_usage_summary(self) -> dict:
    rows = []
    total_data = 0
    total_index = 0
    total_file_bytes = 0
    for i in range(1, self.current_db_index + 1):
        key = f"storage_{i}"
        storage = self.dbs.get(key)
        if storage is None:
            continue
        movie_count = await storage["movie"].count_documents({})
        tv_count = await storage["tv"].count_documents({})
        stream_count = 0
        subtitle_count = 0
        file_bytes = 0
        try:
            async for movie in storage["movie"].find({}, {"telegram": 1, "subtitles": 1}):
                for q in movie.get("telegram") or []:
                    stream_count += 1
                    file_bytes += _tgst_parse_size_to_bytes(q.get("size"))
                subtitle_count += len(movie.get("subtitles") or [])
            async for tv in storage["tv"].find({}, {"seasons": 1}):
                for season in tv.get("seasons") or []:
                    for ep in season.get("episodes") or []:
                        for q in ep.get("telegram") or []:
                            stream_count += 1
                            file_bytes += _tgst_parse_size_to_bytes(q.get("size"))
                        subtitle_count += len(ep.get("subtitles") or [])
        except Exception:
            pass
        try:
            stats = await storage.command("dbStats")
        except Exception:
            stats = {}
        data_size = int(stats.get("dataSize") or 0)
        index_size = int(stats.get("indexSize") or 0)
        total_data += data_size
        total_index += index_size
        total_file_bytes += file_bytes
        rows.append({
            "db": key,
            "movies": movie_count,
            "series": tv_count,
            "streams": stream_count,
            "subtitles": subtitle_count,
            "file_bytes_est": file_bytes,
            "file_size_est": _tgst_format_bytes(file_bytes),
            "data_size": data_size,
            "index_size": index_size,
            "data_readable": _tgst_format_bytes(data_size),
            "index_readable": _tgst_format_bytes(index_size),
        })
    return {
        "summary": {
            "storage_dbs": len(rows),
            "data_size": total_data,
            "index_size": total_index,
            "file_bytes_est": total_file_bytes,
            "data_readable": _tgst_format_bytes(total_data),
            "index_readable": _tgst_format_bytes(total_index),
            "file_size_est": _tgst_format_bytes(total_file_bytes),
        },
        "databases": rows,
    }


async def _database_get_top_watched(self, limit: int = 15) -> dict:
    try:
        col = self.dbs["tracking"]["stream_analytics"]
        pipeline = [
            {"$group": {
                "_id": {"title": "$title", "stream_id": "$stream_id"},
                "plays": {"$sum": 1},
                "bytes": {"$sum": "$total_bytes"},
                "avg_mbps": {"$avg": "$avg_mbps"},
                "last": {"$max": "$logged_at"},
            }},
            {"$sort": {"plays": -1, "bytes": -1}},
            {"$limit": int(limit)},
        ]
        rows = await col.aggregate(pipeline).to_list(None)
        out = []
        for r in rows:
            ident = r.pop("_id", {}) or {}
            out.append({
                "title": ident.get("title") or "Unknown",
                "stream_id": ident.get("stream_id"),
                "plays": int(r.get("plays") or 0),
                "bytes": int(r.get("bytes") or 0),
                "readable": _tgst_format_bytes(r.get("bytes") or 0),
                "avg_mbps": round(float(r.get("avg_mbps") or 0), 2),
                "last": r.get("last").isoformat() if r.get("last") else None,
            })
        return {"items": out}
    except Exception as e:
        LOGGER.warning("top watched stats failed: %s", e)
        return {"items": []}


def _database_decode_chat_from_hash_sync_placeholder(item):
    return None


async def _database_get_source_topic_stats(self) -> dict:
    stats = {}
    def add(chat_id, topic_id, media_type, item_kind="stream"):
        chat = str(chat_id or "unknown")
        topic = str(topic_id or "all")
        key = f"{chat}:{topic}"
        row = stats.setdefault(key, {
            "chat_id": chat,
            "topic_id": None if topic == "all" else topic,
            "name": chat,
            "topic_name": "All topics" if topic == "all" else f"Topic {topic}",
            "movies": 0, "series": 0, "streams": 0, "subtitles": 0,
        })
        if item_kind == "subtitle":
            row["subtitles"] += 1
        else:
            row["streams"] += 1
            if media_type == "movie": row["movies"] += 1
            if media_type == "tv": row["series"] += 1

    for i in range(1, self.current_db_index + 1):
        storage = self.dbs.get(f"storage_{i}")
        if storage is None: continue
        async for movie in storage["movie"].find({}, {"telegram": 1, "subtitles": 1}):
            for q in movie.get("telegram") or []:
                chat = q.get("source_chat_id") or q.get("chat_id") or q.get("channel_id") or q.get("chat")
                topic = q.get("source_topic_id") or q.get("message_thread_id") or q.get("topic_id")
                if not chat and q.get("parts"):
                    chat = (q.get("parts") or [{}])[0].get("chat_id")
                if not topic and q.get("parts"):
                    topic = (q.get("parts") or [{}])[0].get("topic_id") or (q.get("parts") or [{}])[0].get("message_thread_id")
                add(chat, topic, "movie", "stream")
            for sub in movie.get("subtitles") or []:
                add(sub.get("source_chat_id") or sub.get("chat_id") or sub.get("channel_id"), sub.get("source_topic_id") or sub.get("message_thread_id") or sub.get("topic_id"), "movie", "subtitle")
        async for tv in storage["tv"].find({}, {"seasons": 1}):
            for season in tv.get("seasons") or []:
                for ep in season.get("episodes") or []:
                    for q in ep.get("telegram") or []:
                        chat = q.get("source_chat_id") or q.get("chat_id") or q.get("channel_id") or q.get("chat")
                        topic = q.get("source_topic_id") or q.get("message_thread_id") or q.get("topic_id")
                        if not chat and q.get("parts"):
                            chat = (q.get("parts") or [{}])[0].get("chat_id")
                        if not topic and q.get("parts"):
                            topic = (q.get("parts") or [{}])[0].get("topic_id") or (q.get("parts") or [{}])[0].get("message_thread_id")
                        add(chat, topic, "tv", "stream")
                    for sub in ep.get("subtitles") or []:
                        add(sub.get("source_chat_id") or sub.get("chat_id") or sub.get("channel_id"), sub.get("source_topic_id") or sub.get("message_thread_id") or sub.get("topic_id"), "tv", "subtitle")
    rows = list(stats.values())
    rows.sort(key=lambda r: (r.get("streams", 0) + r.get("subtitles", 0)), reverse=True)
    return {"topics": rows, "total": len(rows)}


Database.reset_token_usage = _database_reset_token_usage
Database.get_token_usage_summary = _database_get_token_usage_summary
Database.get_storage_usage_summary = _database_get_storage_usage_summary
Database.get_top_watched = _database_get_top_watched
Database.get_source_topic_stats = _database_get_source_topic_stats
