from os import getenv, path
from dotenv import load_dotenv

load_dotenv(path.join(path.dirname(path.dirname(__file__)), "config.env"))

def _clean_url(value: str) -> str:
    return (value or "").strip().rstrip('/')

def _default_base_url(*, allow_explicit_env: bool = True) -> str:
    """Return a sensible public URL when BASE_URL is not configured.

    Hugging Face Spaces usually injects SPACE_HOST/SPACE_ID at runtime.
    BASE_URL should still be set manually for custom domains or private Spaces.
    """
    explicit = _clean_url(getenv("BASE_URL", "")) if allow_explicit_env else ""
    if explicit:
        return explicit

    space_host = _clean_url(getenv("SPACE_HOST", ""))
    if space_host:
        if space_host.startswith(("http://", "https://")):
            return space_host
        return f"https://{space_host}"

    space_id = (getenv("SPACE_ID", "") or "").strip()
    if "/" in space_id:
        owner, repo = space_id.split("/", 1)
        return f"https://{owner}-{repo}.hf.space"

    return ""

class Telegram:
    API_ID = int(getenv("API_ID", "0"))
    API_HASH = getenv("API_HASH", "")
    BOT_TOKEN = getenv("BOT_TOKEN", "")
    HELPER_BOT_TOKEN = getenv("HELPER_BOT_TOKEN", "")

    BASE_URL = _default_base_url()
    PORT = int(getenv("PORT", "7860"))

    # Streaming parallelism/prefetch are selected dynamically for every request
    # from active viewer count and all connected Telegram stream bot clients.
    # The v3.2 router uses the healthy bot pool across concurrent viewers.

    AUTH_CHANNEL = [channel.strip() for channel in (getenv("AUTH_CHANNEL") or "").split(",") if channel.strip()]
    DATABASE = [db.strip() for db in (getenv("DATABASE") or "").split(",") if db.strip()]

    TMDB_API = getenv("TMDB_API", "")

    # WebUI-managed update defaults. Legacy env values are used only until a
    # value is saved from the dashboard.
    UPSTREAM_REPO = getenv("UPSTREAM_REPO", "https://github.com/weebzone/Telegram-Stremio")
    UPSTREAM_BRANCH = getenv("UPSTREAM_BRANCH", "master")

    OWNER_ID = int(getenv("OWNER_ID", "5422223708"))
    
    REPLACE_MODE = getenv("REPLACE_MODE", "true").lower() == "true"
    HIDE_CATALOG = getenv("HIDE_CATALOG", "false").lower() == "true"

    ADMIN_USERNAME = getenv("ADMIN_USERNAME", "fyvio")
    ADMIN_PASSWORD = getenv("ADMIN_PASSWORD", "fyvio")
    
    SUBSCRIPTION = getenv("SUBSCRIPTION", "false").lower() == "true"
    SUBSCRIPTION_GROUP_ID = int(getenv("SUBSCRIPTION_GROUP_ID", "0"))
    SUBSCRIPTION_URL = getenv("SUBSCRIPTION_URL", "https://t.me/")
    APPROVER_IDS = [int(x.strip()) for x in (getenv("APPROVER_IDS") or "").split(",") if x.strip().isdigit()]

    PROXY = getenv("Proxy", "false").lower() == "true"
    PROXY_TYPE = getenv("ProxyType", "HTTPS")
    HTTP_PROXY_URL = getenv("HTTP_Proxy_URL", "")
    SHOW_PROXY_AND_NON_PROXY_BOTH = getenv("SHOW_ProxyAndNonProxyBoth", "false").lower() == "true"
# Attach newer optional config values without breaking older env files.
Telegram.SPLIT_ZIP_STREAM = getenv("SPLIT_ZIP_STREAM", "true").lower() == "true"
Telegram.SPLIT_ZIP_FINALIZE_SECONDS = int(getenv("SPLIT_ZIP_FINALIZE_SECONDS", "60"))
# Smart built-in defaults for low-RAM virtual split-ZIP streaming.
# No VIRTUAL_ZIP_* Render env variables are required.
Telegram.VIRTUAL_ZIP_MAX_CACHED_BLOCKS = int(getenv("VIRTUAL_ZIP_MAX_CACHED_BLOCKS", "64"))
Telegram.VIRTUAL_ZIP_PREFETCH = int(getenv("VIRTUAL_ZIP_PREFETCH", "8"))
Telegram.VIRTUAL_ZIP_HTTP_CHUNK_MB = int(getenv("VIRTUAL_ZIP_HTTP_CHUNK_MB", "8"))
Telegram.VIRTUAL_ZIP_META_CACHE_SECONDS = int(getenv("VIRTUAL_ZIP_META_CACHE_SECONDS", "1800"))
Telegram.VIRTUAL_ZIP_FORCE_RANGE = getenv("VIRTUAL_ZIP_FORCE_RANGE", "true").lower() == "true"
Telegram.VIRTUAL_ZIP_INITIAL_RANGE_MB = int(getenv("VIRTUAL_ZIP_INITIAL_RANGE_MB", "32"))
# Split stream buffering/cache tuning. Safe built-in defaults; env override is optional only.
Telegram.SPLIT_STREAM_WINDOW_MB = int(getenv("SPLIT_STREAM_WINDOW_MB", str(Telegram.VIRTUAL_ZIP_INITIAL_RANGE_MB)))
Telegram.STREAM_SEEK_WINDOW_MB = int(getenv("STREAM_SEEK_WINDOW_MB", "16"))
Telegram.SPLIT_SEEK_WINDOW_MB = int(getenv("SPLIT_SEEK_WINDOW_MB", "16"))
# One viewer is pinned to one bot; these only tune same-bot read-ahead.
Telegram.STREAM_AFFINITY_SECONDS = int(getenv("STREAM_AFFINITY_SECONDS", "900"))
Telegram.STREAM_PREFETCH_WORKERS = int(getenv("STREAM_PREFETCH_WORKERS", "2"))
Telegram.STREAM_PREFETCH_BLOCKS = int(getenv("STREAM_PREFETCH_BLOCKS", "3"))
Telegram.SPLIT_STREAM_BLOCK_CACHE = getenv("SPLIT_STREAM_BLOCK_CACHE", "true").lower() == "true"
Telegram.SPLIT_STREAM_CACHE_MAX_MB = int(getenv("SPLIT_STREAM_CACHE_MAX_MB", "2048"))
# Normal single-file stream cache. Helps repeated VLC/Stremio probes and seeks; no env needed.
Telegram.STREAM_BLOCK_CACHE = getenv("STREAM_BLOCK_CACHE", "true").lower() == "true"
Telegram.STREAM_CACHE_MAX_MB = int(getenv("STREAM_CACHE_MAX_MB", "2048"))
Telegram.STREAM_CACHE_DIR = getenv("STREAM_CACHE_DIR", "/tmp/tg_stream_cache")
# Legacy compatibility only. The v3.2 router assigns one bot per viewer and
# uses the full healthy pool across concurrent viewers.
Telegram.STREAM_MAX_PARALLEL_BOTS = int(getenv("STREAM_MAX_PARALLEL_BOTS", "0") or 0)

# Direct stream startup/resume tuning. Safe built-in defaults; env override is optional only.
Telegram.STREAM_FORCE_RANGE = getenv("STREAM_FORCE_RANGE", "true").lower() == "true"
Telegram.STREAM_INITIAL_RANGE_MB = int(getenv("STREAM_INITIAL_RANGE_MB", "32"))
# Deprecated compatibility values from older extract/cache split-ZIP builds.
Telegram.SPLIT_ZIP_CACHE_DIR = getenv("SPLIT_ZIP_CACHE_DIR", "/tmp/tg_split_zip_cache")
Telegram.SPLIT_ZIP_DELETE_ARCHIVE_AFTER_EXTRACT = getenv("SPLIT_ZIP_DELETE_ARCHIVE_AFTER_EXTRACT", "true").lower() == "true"
