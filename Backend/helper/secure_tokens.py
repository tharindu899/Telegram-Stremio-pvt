"""Protected storage helpers for WebUI-managed extra Telegram bot tokens.

The main ``API_HASH`` and ``BOT_TOKEN`` remain startup secrets.  Those two
secrets derive the encryption key used to store optional stream-bot tokens in
MongoDB.  The browser never receives a saved token back from the API.
"""
from __future__ import annotations

import json
import re
from base64 import urlsafe_b64encode
from hashlib import sha256
from typing import Any, Iterable

from cryptography.fernet import Fernet, InvalidToken

from Backend.config import Telegram

# Telegram bot tokens are generally ``123456:ABC…``.  This accepts current
# BotFather token formats while still catching common paste mistakes.
BOT_TOKEN_RE = re.compile(r"^\d{5,20}:[A-Za-z0-9_-]{20,160}$")


class BotTokenValidationError(ValueError):
    """Raised when the WebUI receives an invalid extra bot token."""


def _iter_raw_values(value: Any) -> Iterable[str]:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_raw_values(item)
        return

    text = str(value or "")
    for line in text.replace("\r", "").split("\n"):
        # Also allow a comma-separated paste or ``MULTI_TOKEN1=...`` lines.
        for item in line.split(","):
            candidate = item.strip()
            if not candidate:
                continue
            if "=" in candidate:
                left, right = candidate.split("=", 1)
                if left.strip().upper().startswith("MULTI_TOKEN"):
                    candidate = right.strip()
            yield candidate


def parse_bot_tokens(value: Any) -> list[str]:
    """Normalize, validate and de-duplicate one or more extra bot tokens."""
    tokens: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []

    for token in _iter_raw_values(value):
        if not BOT_TOKEN_RE.fullmatch(token):
            invalid.append(token)
            continue
        fingerprint = token_id(token)
        if fingerprint not in seen:
            tokens.append(token)
            seen.add(fingerprint)

    if invalid:
        preview = ", ".join(mask_token(token) for token in invalid[:3])
        raise BotTokenValidationError(
            f"Invalid Telegram bot token: {preview}. Paste one complete BotFather token per field."
        )
    return tokens


def token_id(token: str) -> str:
    """Stable non-secret ID used by the UI when it removes a saved bot."""
    return sha256(str(token or "").encode("utf-8")).hexdigest()[:20]


def mask_token(token: str) -> str:
    token = str(token or "")
    if not token:
        return "empty"
    if ":" not in token:
        return "••••"
    bot_id, secret = token.split(":", 1)
    return f"{bot_id}:••••{secret[-4:]}" if secret else f"{bot_id}:••••"


def _fernet() -> Fernet:
    """Create a deterministic key from required startup secrets.

    If the app's API hash or primary bot token is deliberately replaced, saved
    multi-bot tokens must be entered again.  This is preferable to silently
    keeping an unencrypted copy in the database.
    """
    api_hash = str(getattr(Telegram, "API_HASH", "") or "").strip()
    bot_token = str(getattr(Telegram, "BOT_TOKEN", "") or "").strip()
    if not api_hash or not bot_token:
        raise RuntimeError("API_HASH and BOT_TOKEN are required before multi-bot tokens can be stored.")
    material = sha256(f"telegram-stremio-multi-bots\0{api_hash}\0{bot_token}".encode("utf-8")).digest()
    return Fernet(urlsafe_b64encode(material))


def encrypt_bot_tokens(tokens: Any) -> str:
    normalized = parse_bot_tokens(tokens)
    payload = json.dumps({"v": 1, "tokens": normalized}, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(payload).decode("utf-8")


def decrypt_bot_tokens(encrypted_value: Any) -> list[str]:
    if not encrypted_value:
        return []
    try:
        raw = _fernet().decrypt(str(encrypted_value).encode("utf-8"))
        payload = json.loads(raw.decode("utf-8"))
        return parse_bot_tokens((payload or {}).get("tokens", []))
    except InvalidToken as exc:
        raise RuntimeError(
            "Saved multi-bot tokens cannot be decrypted. Re-enter them in Admin → Config."
        ) from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Saved multi-bot token data is invalid. Re-enter the token list in Admin → Config.") from exc
