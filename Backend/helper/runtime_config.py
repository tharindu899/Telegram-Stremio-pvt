"""Live WebUI configuration for Telegram-Stremio.

Only connection credentials that are needed *before* MongoDB is available stay in
``config.env`` / hosting secrets.  All operational settings below are persisted
in MongoDB, loaded on startup, and applied immediately when saved from the
admin WebUI.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict
from urllib.parse import urlparse

from Backend.config import Telegram, _default_base_url
from Backend.logger import LOGGER

# These are the only values that must exist before the application can connect
# to Telegram and MongoDB.  They are intentionally never part of CONFIG_SCHEMA.
STARTUP_CRITICAL = {
    "API_ID", "API_HASH", "BOT_TOKEN", "HELPER_BOT_TOKEN", "OWNER_ID", "DATABASE", "PORT"
}

# Clean up config entries created by older project builds. Streaming now tunes
# parallelism and prefetch automatically. ``MULTI_BOT_TOKENS`` is removed from
# the generic config document because WebUI bot tokens now use their own
# encrypted collection.
LEGACY_RUNTIME_KEYS = {"PARALLEL", "PRE_FETCH", "MULTI_BOT_TOKENS"}

CONFIG_SCHEMA: Dict[str, Dict[str, Any]] = {
    "BASE_URL": {
        "label": "Public Base URL", "type": "str", "category": "General",
        "description": "Your public app URL. Leave empty on Hugging Face to auto-detect the Space URL.",
        "placeholder": "https://username-space.hf.space", "attr": "BASE_URL",
    },
    "AUTH_CHANNEL": {
        "label": "Source Channel / Group IDs", "type": "list_str", "category": "Telegram Sources",
        "description": "One source per line or comma-separated. Use the parent -100… ID for topic groups; add both bots as channel admins.",
        "placeholder": "-1001234567890\n-1009876543210", "attr": "AUTH_CHANNEL",
    },
    "TMDB_API": {
        "label": "TMDB API Key", "type": "secret", "category": "Metadata",
        "description": "Used for movie and series lookup. New matches use the key immediately after saving.",
        "placeholder": "tmdb_api_key", "attr": "TMDB_API",
    },
    "REPLACE_MODE": {
        "label": "Replace Same Release", "type": "bool", "category": "Library",
        "description": "Replace an older identical release instead of keeping duplicate stream entries.",
        "attr": "REPLACE_MODE",
    },
    "HIDE_CATALOG": {
        "label": "Hide Default Catalogs", "type": "bool", "category": "Library",
        "description": "Hide built-in Telegram movie and series catalogs from the Stremio manifest.",
        "attr": "HIDE_CATALOG",
    },
    "ADMIN_USERNAME": {
        "label": "Admin Username", "type": "str", "category": "Admin Access",
        "description": "New admin logins use this username immediately. Do not keep the default value.",
        "placeholder": "admin", "attr": "ADMIN_USERNAME",
    },
    "ADMIN_PASSWORD": {
        "label": "Admin Password", "type": "secret", "category": "Admin Access",
        "description": "New admin logins use this password immediately. Existing sessions remain signed in.",
        "placeholder": "strong_password", "attr": "ADMIN_PASSWORD",
    },
    "SUBSCRIPTION": {
        "label": "Subscription Mode", "type": "bool", "category": "Subscription",
        "description": "Require an active user subscription before returning playable streams.",
        "attr": "SUBSCRIPTION",
    },
    "SUBSCRIPTION_GROUP_ID": {
        "label": "Subscriber Group ID", "type": "int", "category": "Subscription",
        "description": "Private Telegram group/channel that approved subscribers can join.",
        "placeholder": "-1001234567890", "attr": "SUBSCRIPTION_GROUP_ID",
    },
    "SUBSCRIPTION_URL": {
        "label": "Renewal / Payment URL", "type": "str", "category": "Subscription",
        "description": "Bot or payment URL shown to a user when their subscription has expired.",
        "placeholder": "https://t.me/yourbot", "attr": "SUBSCRIPTION_URL",
    },
    "APPROVER_IDS": {
        "label": "Payment Approver IDs", "type": "list_int", "category": "Subscription",
        "description": "Telegram user IDs allowed to approve or reject payments. One per line or comma-separated.",
        "placeholder": "123456789\n987654321", "attr": "APPROVER_IDS",
    },
    "UPSTREAM_REPO": {
        "label": "Update Repository", "type": "str", "category": "Updates",
        "description": "Git repository used by update tools. Keep the upstream default unless you maintain your own fork.",
        "placeholder": "https://github.com/weebzone/Telegram-Stremio", "attr": "UPSTREAM_REPO",
    },
    "UPSTREAM_BRANCH": {
        "label": "Update Branch", "type": "str", "category": "Updates",
        "description": "Branch checked by update tools.",
        "placeholder": "master", "attr": "UPSTREAM_BRANCH",
    },
    "Proxy": {
        "label": "Enable Proxy Links", "type": "bool", "category": "Proxy",
        "description": "Create proxy stream URLs instead of only direct Telegram stream URLs.", "attr": "PROXY",
    },
    "ProxyType": {
        "label": "Proxy Type", "type": "choice", "choices": ["HTTPS", "HTTP"], "category": "Proxy",
        "description": "Protocol advertised for generated proxy links.", "attr": "PROXY_TYPE",
    },
    "HTTP_Proxy_URL": {
        "label": "Proxy URL Prefix", "type": "str", "category": "Proxy",
        "description": "Full prefix that receives the encoded stream URL, for example https://proxy.example/?url=.",
        "placeholder": "https://proxy.example/?url=", "attr": "HTTP_PROXY_URL",
    },
    "SHOW_ProxyAndNonProxyBoth": {
        "label": "Show Proxy and Direct", "type": "bool", "category": "Proxy",
        "description": "Show both proxy and direct options in Stremio when proxy links are enabled.", "attr": "SHOW_PROXY_AND_NON_PROXY_BOTH",
    },
    "STREAM_FORCE_RANGE": {
        "label": "Fast Start & Resume Windows", "type": "bool", "category": "Streaming",
        "description": "Use player-friendly HTTP range windows to start playback faster and improve seeking.", "attr": "STREAM_FORCE_RANGE",
    },
    "STREAM_INITIAL_RANGE_MB": {
        "label": "Direct Stream Window (MB)", "type": "int", "category": "Streaming",
        "description": "Cold-play response window for normal files. Lower values help quick restart; recommended: 24–48 MB.",
        "placeholder": "32", "attr": "STREAM_INITIAL_RANGE_MB",
    },
    "SPLIT_STREAM_WINDOW_MB": {
        "label": "Split File Window (MB)", "type": "int", "category": "Streaming",
        "description": "Cold-play response window for direct split videos and stored ZIP entries. Recommended: 24–48 MB.",
        "placeholder": "32", "attr": "SPLIT_STREAM_WINDOW_MB",
    },
    "STREAM_SEEK_WINDOW_MB": {
        "label": "Direct Seek Window (MB)", "type": "int", "category": "Streaming",
        "description": "Small response window used after seeking in a normal file. Recommended: 8–24 MB.",
        "placeholder": "16", "attr": "STREAM_SEEK_WINDOW_MB",
    },
    "SPLIT_SEEK_WINDOW_MB": {
        "label": "Split Seek Window (MB)", "type": "int", "category": "Streaming",
        "description": "Small response window used after seeking in a direct split file or stored ZIP entry. Recommended: 8–24 MB.",
        "placeholder": "16", "attr": "SPLIT_SEEK_WINDOW_MB",
    },
    "STREAM_AFFINITY_SECONDS": {
        "label": "Bot Stickiness (seconds)", "type": "int", "category": "Streaming",
        "description": "Keep one viewer on the same healthy bot for play, resume, and seek. Other viewers are balanced across remaining bots.",
        "placeholder": "900", "attr": "STREAM_AFFINITY_SECONDS",
    },
    "STREAM_PREFETCH_WORKERS": {
        "label": "Same-Bot Read Workers", "type": "int", "category": "Streaming",
        "description": "Concurrent GetFile reads through the assigned bot only. Recommended: 1–2.",
        "placeholder": "2", "attr": "STREAM_PREFETCH_WORKERS",
    },
    "STREAM_PREFETCH_BLOCKS": {
        "label": "Low Buffer Blocks", "type": "int", "category": "Streaming",
        "description": "1 MB blocks buffered ahead for each response. Recommended: 2–4.",
        "placeholder": "3", "attr": "STREAM_PREFETCH_BLOCKS",
    },
}

DEFAULT_VALUES = {
    "BASE_URL": "",
    "AUTH_CHANNEL": "",
    "TMDB_API": "",
    "REPLACE_MODE": True,
    "HIDE_CATALOG": False,
    "ADMIN_USERNAME": "fyvio",
    "ADMIN_PASSWORD": "fyvio",
    "SUBSCRIPTION": False,
    "SUBSCRIPTION_GROUP_ID": 0,
    "SUBSCRIPTION_URL": "https://t.me/",
    "APPROVER_IDS": "",
    "UPSTREAM_REPO": "https://github.com/weebzone/Telegram-Stremio",
    "UPSTREAM_BRANCH": "master",
    "Proxy": False,
    "ProxyType": "HTTPS",
    "HTTP_Proxy_URL": "",
    "SHOW_ProxyAndNonProxyBoth": False,
    "STREAM_FORCE_RANGE": True,
    "STREAM_INITIAL_RANGE_MB": 32,
    "SPLIT_STREAM_WINDOW_MB": 32,
    "STREAM_SEEK_WINDOW_MB": 16,
    "SPLIT_SEEK_WINDOW_MB": 16,
    "STREAM_AFFINITY_SECONDS": 900,
    "STREAM_PREFETCH_WORKERS": 2,
    "STREAM_PREFETCH_BLOCKS": 3,
}


def env_is_defined(key: str) -> bool:
    """Compatibility helper for callers that inspect legacy environment values."""
    value = os.getenv(key)
    return value is not None and str(value).strip() != ""


def is_webui_locked(key: str) -> bool:
    """Return True only for startup-critical values.

    No startup-critical field is present in CONFIG_SCHEMA, so all controls shown
    in the Config page are intentionally editable even when an old deployment
    still has legacy environment variables set.
    """
    return key in STARTUP_CRITICAL


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _parse_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def _parse_list_str(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").replace("\n", ",").split(",")
    return [str(v).strip() for v in raw if str(v).strip()]


def _parse_list_int(value: Any) -> list[int]:
    out: list[int] = []
    for item in _parse_list_str(value):
        try:
            out.append(int(item))
        except Exception:
            pass
    return out


def serialize_value(key: str, value: Any) -> Any:
    schema = CONFIG_SCHEMA.get(key, {})
    field_type = schema.get("type", "str")
    if field_type == "bool":
        return _parse_bool(value)
    if field_type == "int":
        return _parse_int(value)
    if field_type == "list_int":
        return ",".join(str(x) for x in _parse_list_int(value)) if isinstance(value, list) else str(value or "")
    if field_type == "list_str":
        return ",".join(_parse_list_str(value)) if isinstance(value, list) else str(value or "")
    if field_type == "choice":
        selected = str(value or DEFAULT_VALUES.get(key, "")).strip()
        choices = schema.get("choices") or []
        return selected if selected in choices else (choices[0] if choices else selected)
    return str(value or "")


def parse_value_for_runtime(key: str, value: Any) -> Any:
    schema = CONFIG_SCHEMA.get(key, {})
    field_type = schema.get("type", "str")
    if field_type == "bool":
        return _parse_bool(value)
    if field_type == "int":
        return _parse_int(value)
    if field_type == "list_int":
        return _parse_list_int(value)
    if field_type == "list_str":
        return _parse_list_str(value)
    if field_type == "choice":
        return serialize_value(key, value)
    return str(value or "").strip()


def value_from_telegram(key: str) -> Any:
    schema = CONFIG_SCHEMA[key]
    attr = schema.get("attr")
    value = getattr(Telegram, attr, DEFAULT_VALUES.get(key, ""))
    if schema["type"] in {"list_str", "list_int"} and isinstance(value, list):
        return ",".join(str(v) for v in value)
    return value


def _valid_http_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_runtime_config(values: Dict[str, Any]) -> Dict[str, str]:
    """Validate editable settings before persisting them.

    Validation only blocks malformed values. Empty optional fields remain valid,
    making it possible to turn a feature off and clear its companion fields in
    the same save.
    """
    values = values or {}
    errors: Dict[str, str] = {}

    for key in ("BASE_URL", "SUBSCRIPTION_URL", "HTTP_Proxy_URL"):
        if not _valid_http_url(values.get(key, "")):
            errors[key] = "Enter a complete http:// or https:// URL."

    repo = str(values.get("UPSTREAM_REPO", "") or "").strip()
    if repo and not (_valid_http_url(repo) or repo.startswith("git@")):
        errors["UPSTREAM_REPO"] = "Use an https:// repository URL or a git@ SSH URL."

    for raw_id in _parse_list_str(values.get("AUTH_CHANNEL", "")):
        try:
            if int(raw_id) == 0:
                raise ValueError
        except Exception:
            errors["AUTH_CHANNEL"] = f"Invalid Telegram source ID: {raw_id}"
            break

    for raw_id in _parse_list_str(values.get("APPROVER_IDS", "")):
        try:
            if int(raw_id) <= 0:
                raise ValueError
        except Exception:
            errors["APPROVER_IDS"] = f"Invalid Telegram user ID: {raw_id}"
            break

    for key in ("STREAM_INITIAL_RANGE_MB", "SPLIT_STREAM_WINDOW_MB", "STREAM_SEEK_WINDOW_MB", "SPLIT_SEEK_WINDOW_MB"):
        raw = str(values.get(key, "") or "").strip()
        try:
            number = int(raw)
            if not 4 <= number <= 512:
                raise ValueError
        except Exception:
            errors[key] = "Use a whole number from 4 to 512 MB."

    for key, minimum, maximum in (
        ("STREAM_AFFINITY_SECONDS", 60, 3600),
        ("STREAM_PREFETCH_WORKERS", 1, 4),
        ("STREAM_PREFETCH_BLOCKS", 2, 12),
    ):
        try:
            number = int(str(values.get(key, "") or "").strip())
            if not minimum <= number <= maximum:
                raise ValueError
        except Exception:
            errors[key] = f"Use a whole number from {minimum} to {maximum}."

    username = str(values.get("ADMIN_USERNAME", "") or "").strip()
    password = str(values.get("ADMIN_PASSWORD", "") or "")
    if not username:
        errors["ADMIN_USERNAME"] = "Admin username cannot be empty."
    if len(password) < 4:
        errors["ADMIN_PASSWORD"] = "Use an admin password with at least 4 characters."

    subscription_on = _parse_bool(values.get("SUBSCRIPTION", False))
    group_id = _parse_int(values.get("SUBSCRIPTION_GROUP_ID", 0))
    if subscription_on and not group_id:
        errors["SUBSCRIPTION_GROUP_ID"] = "Set the subscriber group ID before enabling subscription mode."
    if subscription_on and not str(values.get("SUBSCRIPTION_URL", "") or "").strip():
        errors["SUBSCRIPTION_URL"] = "Set a renewal/payment URL before enabling subscription mode."

    proxy_on = _parse_bool(values.get("Proxy", False))
    proxy_url = str(values.get("HTTP_Proxy_URL", "") or "").strip()
    if proxy_on and not proxy_url:
        errors["HTTP_Proxy_URL"] = "Set the proxy URL prefix before enabling proxy links."

    return errors


def apply_runtime_config(values: Dict[str, Any], *, source: str = "webui") -> Dict[str, Any]:
    """Apply WebUI values to the in-memory Telegram configuration immediately."""
    applied: Dict[str, Any] = {}
    for key, raw in (values or {}).items():
        if key not in CONFIG_SCHEMA or is_webui_locked(key):
            continue
        parsed = parse_value_for_runtime(key, raw)
        attr = CONFIG_SCHEMA[key].get("attr")
        setattr(Telegram, attr, parsed)
        applied[key] = serialize_value(key, parsed)

        if key == "BASE_URL" and not parsed:
            # A blank WebUI value means Space/custom-host auto-detection, not a
            # fallback to a legacy BASE_URL environment variable.
            Telegram.BASE_URL = _default_base_url(allow_explicit_env=False)
            applied[key] = Telegram.BASE_URL

        if key == "TMDB_API":
            try:
                from themoviedb import aioTMDb
                import Backend.helper.metadata as metadata
                metadata.tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")
            except Exception as exc:
                LOGGER.warning(f"[RuntimeConfig] TMDB client refresh skipped: {exc}")

    if applied:
        LOGGER.info(
            "[RuntimeConfig] Applied %s live setting(s) from %s: %s",
            len(applied), source, ", ".join(applied),
        )
    return applied


def build_config_payload(db_values: Dict[str, Any] | None = None) -> Dict[str, Any]:
    db_values = db_values or {}
    editable = []

    for key, schema in CONFIG_SCHEMA.items():
        locked = is_webui_locked(key)
        if key == "AUTH_CHANNEL":
            # Keep bot command changes and WebUI edits in one active source list.
            value = value_from_telegram(key)
            source = "Saved in WebUI" if key in db_values else "Current runtime"
        elif key in db_values:
            value = db_values.get(key)
            source = "Saved in WebUI"
        else:
            value = value_from_telegram(key)
            source = "Built-in default"

        field_type = schema.get("type", "str")
        editable.append({
            "key": key,
            "label": schema.get("label", key),
            "type": field_type,
            "category": schema.get("category", "Other"),
            "description": schema.get("description", ""),
            "placeholder": schema.get("placeholder", ""),
            "choices": schema.get("choices", []),
            "value": serialize_value(key, value),
            "env_locked": locked,
            "source": source,
            "secret": field_type == "secret",
        })

    startup = []
    for key in ("API_ID", "API_HASH", "BOT_TOKEN", "HELPER_BOT_TOKEN", "OWNER_ID", "DATABASE", "PORT"):
        raw_env = os.getenv(key, "")
        if key == "DATABASE":
            present = len(getattr(Telegram, "DATABASE", [])) == 2
            display = "configured" if present else "needs exactly 2 URIs"
        elif key in {"API_HASH", "BOT_TOKEN", "HELPER_BOT_TOKEN"}:
            present = bool(str(raw_env).strip())
            display = "configured" if present else "missing"
        elif key == "API_ID":
            present = bool(getattr(Telegram, "API_ID", 0))
            display = str(getattr(Telegram, "API_ID", 0) or "missing")
        elif key == "OWNER_ID":
            present = bool(getattr(Telegram, "OWNER_ID", 0))
            display = str(getattr(Telegram, "OWNER_ID", 0) or "missing")
        else:
            present = bool(str(raw_env or getattr(Telegram, key, "")).strip())
            display = str(getattr(Telegram, key, raw_env or "missing"))
        startup.append({"key": key, "present": present, "value": display, "editable": False})

    return {
        "startup_critical": startup,
        "editable": editable,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "note": "Only Telegram credentials, the two MongoDB URIs, and the server port remain startup-only. All settings below save to MongoDB and apply live.",
        "dynamic_streaming": "Parallel Telegram requests and prefetch queues now scale automatically from active streams and available bot clients.",
    }
