"""Telegram stream clients from host ``MULTI_TOKEN*`` values and WebUI tokens.

Legacy environment tokens remain supported.  Extra bot tokens entered in the
Admin Config page are encrypted in MongoDB, loaded at startup, and can be added
or removed without restarting the main bot.
"""
from __future__ import annotations

from asyncio import Lock, create_task, gather
from os import environ
from typing import Any

from pyrogram import Client

from Backend.config import Telegram
from Backend.helper.secure_tokens import token_id
from Backend.logger import LOGGER
from Backend.pyrofork.bot import (
    StreamBot,
    client_avg_mbps,
    client_chunk_loads,
    client_rtt_ms,
    client_dc_map,
    client_failures,
    multi_clients,
    work_loads,
)

# Only opaque token fingerprints are retained in memory for reconciliation.
_extra_client_token_ids: dict[int, str] = {}
_extra_client_sources: dict[int, str] = {}
_extra_clients_lock = Lock()


class TokenParser:
    """Read legacy ``MULTI_TOKEN1``, ``MULTI_TOKEN2`` … host secrets."""

    @staticmethod
    def parse_from_env() -> list[str]:
        entries: list[tuple[str, str]] = []
        for key, value in environ.items():
            if not key.startswith("MULTI_TOKEN"):
                continue
            token = str(value or "").strip()
            if token:
                entries.append((key, token))
        return [token for _, token in sorted(entries, key=lambda item: item[0])]


async def _capture_dc(client_id: int, client: Client) -> None:
    try:
        client_dc = await client.storage.dc_id()
        client_dc_map[client_id] = client_dc
        LOGGER.info(f"Stream client {client_id} connected to DC {client_dc}")
    except Exception as exc:
        LOGGER.warning(f"Could not get DC for stream client {client_id}: {exc}")
        client_dc_map[client_id] = None


async def start_client(client_id: int, token: str):
    """Start one optional stream-only bot without exposing its token in logs."""
    try:
        LOGGER.info(f"Starting stream client {client_id}")
        client = await Client(
            name=f"stream_{client_id}",
            api_id=Telegram.API_ID,
            api_hash=Telegram.API_HASH,
            bot_token=token,
            sleep_threshold=100,
            no_updates=True,
            in_memory=True,
        ).start()
        await _capture_dc(client_id, client)
        work_loads[client_id] = 0
        client_failures.setdefault(client_id, 0)
        client_avg_mbps.setdefault(client_id, 0.0)
        client_chunk_loads.setdefault(client_id, 0)
        client_rtt_ms.setdefault(client_id, 0.0)
        return client_id, client
    except Exception as exc:
        LOGGER.error(f"Failed to start stream client {client_id}: {exc}")
        return None


def _next_available_client_id() -> int:
    client_id = 1
    while client_id in multi_clients:
        client_id += 1
    return client_id


def _desired_tokens(webui_tokens: list[str] | None) -> dict[str, tuple[str, str]]:
    """Merge host and WebUI tokens, de-duplicating without retaining raw logs."""
    desired: dict[str, tuple[str, str]] = {}
    blocked = {
        token_id(token)
        for token in (getattr(Telegram, "BOT_TOKEN", ""), getattr(Telegram, "HELPER_BOT_TOKEN", ""))
        if str(token or "").strip()
    }

    # Host-secret tokens win when the same token also exists in WebUI storage.
    for source, tokens in (("Host secret", TokenParser.parse_from_env()), ("WebUI", webui_tokens or [])):
        for token in tokens:
            fingerprint = token_id(token)
            if fingerprint in blocked:
                LOGGER.warning("Skipping an extra stream token that duplicates the main or helper bot.")
                continue
            desired.setdefault(fingerprint, (token, source))
    return desired


async def _stop_extra_client(client_id: int) -> bool:
    client = multi_clients.pop(client_id, None)
    _extra_client_token_ids.pop(client_id, None)
    _extra_client_sources.pop(client_id, None)
    work_loads.pop(client_id, None)
    client_dc_map.pop(client_id, None)
    client_failures.pop(client_id, None)
    client_avg_mbps.pop(client_id, None)
    client_chunk_loads.pop(client_id, None)
    client_rtt_ms.pop(client_id, None)
    if not client:
        return False
    try:
        if getattr(client, "is_connected", False):
            await client.stop()
    except Exception as exc:
        LOGGER.warning(f"Could not stop removed stream client {client_id}: {exc}")
    return True


async def sync_extra_clients(webui_tokens: list[str] | None = None, *, source: str = "webui") -> dict[str, Any]:
    """Reconcile optional streaming clients with host and WebUI token lists.

    The primary stream bot at index 0 is never stopped or replaced.  A removed
    extra client is unregistered immediately; any already-open playback request
    may retry through another available bot.
    """
    async with _extra_clients_lock:
        multi_clients[0] = StreamBot
        work_loads.setdefault(0, 0)
        client_chunk_loads.setdefault(0, 0)
        client_rtt_ms.setdefault(0, 0.0)

        desired = _desired_tokens(webui_tokens)
        current_by_fingerprint = {fingerprint: index for index, fingerprint in _extra_client_token_ids.items()}
        removed = 0
        added = 0

        for fingerprint, client_id in list(current_by_fingerprint.items()):
            if fingerprint not in desired:
                removed += int(await _stop_extra_client(client_id))

        for fingerprint, (token, token_source) in desired.items():
            existing_id = current_by_fingerprint.get(fingerprint)
            if existing_id in multi_clients:
                _extra_client_sources[existing_id] = token_source
                continue

            client_id = _next_available_client_id()
            started = await start_client(client_id, token)
            if not started:
                continue
            _, client = started
            multi_clients[client_id] = client
            _extra_client_token_ids[client_id] = fingerprint
            _extra_client_sources[client_id] = token_source
            added += 1

        if added or removed:
            LOGGER.info(
                "Multi-client pool updated from %s: +%s / -%s, %s total clients active.",
                source,
                added,
                removed,
                len(multi_clients),
            )

        return {
            "added": added,
            "removed": removed,
            "connected": max(0, len(multi_clients) - 1),
            "total_clients": len(multi_clients),
        }


async def initialize_clients():
    """Start the owner StreamBot plus host/WebUI-managed stream-only clients."""
    multi_clients[0], work_loads[0] = StreamBot, 0
    client_chunk_loads.setdefault(0, 0)
    client_rtt_ms.setdefault(0, 0.0)
    await _capture_dc(0, StreamBot)

    saved_tokens: list[str] = []
    try:
        from Backend import db
        saved_tokens = await db.get_multi_bot_tokens()
    except Exception as exc:
        LOGGER.warning(f"Could not load WebUI multi-bot tokens during startup: {exc}")

    result = await sync_extra_clients(saved_tokens, source="startup")
    if len(multi_clients) != 1:
        LOGGER.info(f"Multi-Client Mode Enabled with {len(multi_clients)} clients")
        LOGGER.info(f"DC Distribution: {client_dc_map}")
    else:
        LOGGER.info("No additional clients were initialized, using default client")
    return result


def get_multi_bot_runtime_status() -> dict[str, Any]:
    """Safe status data for the admin page; never includes token values."""
    clients = []
    for client_id in sorted(_extra_client_token_ids):
        client = multi_clients.get(client_id)
        clients.append({
            "client_id": client_id,
            "source": _extra_client_sources.get(client_id, "WebUI"),
            "connected": bool(client and getattr(client, "is_connected", False)),
        })
    return {
        "primary_connected": bool(multi_clients.get(0)),
        "extra_connected": len(clients),
        "total_connected": len(multi_clients),
        "clients": clients,
        "host_secret_count": len(TokenParser.parse_from_env()),
    }
