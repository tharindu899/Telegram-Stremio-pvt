"""Low-RAM helpers for split archives stored on Telegram.

This module implements the "virtual reader" style used by lightweight
Telegram/Stremio proxies. Files named like ``Movie.zip.001`` or
``Movie.mkv.001`` are treated as one continuous byte stream. ZIP directory
reads and playable entry reads are mapped to Telegram range/chunk fetches, so
the server does not need to concatenate or extract the full archive to disk.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import zipfile
from collections import OrderedDict
from pathlib import Path
from threading import Thread
from typing import Any, Dict, Iterable, List, Optional, Union

import anyio
LOGGER = logging.getLogger("split_archive")

SPLIT_ZIP_RE = re.compile(r"(?P<base>[^\\/\n\r]+?\.zip)\.(?P<part>\d{1,3})(?!\d)", re.IGNORECASE)
SPLIT_VIDEO_RE = re.compile(
    r"(?P<base>[^\\/\n\r]+?\.(?:mkv|mp4|webm|mov|avi|m4v|ts|m2ts|wmv|flv))\.(?P<part>\d{1,3})(?!\d)",
    re.IGNORECASE,
)
# Legacy uploader format: Movie.part001.mkv / Movie Part 2.mp4.
# These parts are a single continuous video too, so group them before normal
# metadata parsing instead of treating each part as a broken standalone file.
SPLIT_VIDEO_LEGACY_RE = re.compile(
    r"(?P<stem>[^\\/\n\r]+)(?:[._ -](?:part|pt|cd|disc|disk)[._ -]*)(?P<part>\d{1,3})(?P<ext>\.(?:mkv|mp4|webm|mov|avi|m4v|ts|m2ts|wmv|flv))(?=$|\s|[\]\)])",
    re.IGNORECASE,
)
VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".webm", ".mov", ".avi", ".m4v", ".ts", ".m2ts", ".wmv", ".flv"
}

# Telegram often sends a video uploaded as a "File" with the generic
# application/octet-stream MIME type.  Do not rely on MIME alone: the actual
# filename is the dependable signal for these media documents.
_VIDEO_FILENAME_RE = re.compile(
    r"(?i)\.(?:mkv|mp4|webm|mov|avi|m4v|ts|m2ts|wmv|flv)(?=$|[\s\]\)\}>,;:!?])"
)


def is_video_filename(value: str | None) -> bool:
    """Return True when *value* contains a supported video filename.

    This accepts a plain Telegram filename and a filename placed in a caption,
    while avoiding broad MIME-only checks that reject valid .mkv documents.
    """
    if not value:
        return False
    text = str(value).strip().replace("\\", "/")
    return bool(_VIDEO_FILENAME_RE.search(os.path.basename(text)) or _VIDEO_FILENAME_RE.search(text))


def _clean_detected_split_base(base: str) -> str:
    """Clean labels when a filename was detected from a caption line."""
    base = (base or "").strip().strip('`"\' ')
    # Examples: "File: Movie.mkv" / "Filename - Movie.zip"
    label_match = re.search(r"(?i)(?:file|filename|name|video|source)\s*[:：=\-]\s*(.+)$", base)
    if label_match:
        base = label_match.group(1).strip().strip('`"\' ')
    return base


def split_zip_info(filename: str | None) -> Optional[Dict[str, Any]]:
    """Return split ZIP info for names like ``Movie.zip.001``.

    Also detects the filename when it is placed in a Telegram caption, e.g.
    ``File: Movie.zip.001``. Part numbers may be ``1`` or ``001``.
    """
    if not filename:
        return None
    text = str(filename).strip().replace("\\", "/")
    # Try basename first for real file names, then the full text for captions.
    candidates = [os.path.basename(text), text]
    for name in candidates:
        match = SPLIT_ZIP_RE.search(name)
        if not match:
            continue
        part_raw = match.group("part")
        return {
            "base_name": _clean_detected_split_base(match.group("base")),
            "part_number": int(part_raw),
            "part_suffix": part_raw.zfill(3),
        }
    return None


def strip_split_zip_suffix(filename: str) -> str:
    """Convert ``Movie.zip.001`` or ``Movie.zip`` to ``Movie``."""
    info = split_zip_info(filename)
    base = info["base_name"] if info else filename
    if base.lower().endswith(".zip"):
        base = base[:-4]
    return base


def split_video_info(filename: str | None) -> Optional[Dict[str, Any]]:
    """Return direct split video information from common Telegram naming styles.

    Supported forms include ``Movie.mkv.001`` and legacy uploader names such
    as ``Movie.part001.mkv`` / ``Movie Part 2.mp4``. Captions are also checked.
    """
    if not filename:
        return None
    text = str(filename).strip().replace("\\", "/")
    candidates = [os.path.basename(text), text]
    for name in candidates:
        match = SPLIT_VIDEO_RE.search(name)
        if match:
            part_raw = match.group("part")
            return {
                "base_name": _clean_detected_split_base(match.group("base")),
                "part_number": int(part_raw),
                "part_suffix": part_raw.zfill(3),
            }

        legacy = SPLIT_VIDEO_LEGACY_RE.search(name)
        if legacy:
            part_raw = legacy.group("part")
            base_name = f"{legacy.group('stem')}{legacy.group('ext')}"
            return {
                "base_name": _clean_detected_split_base(base_name),
                "part_number": int(part_raw),
                "part_suffix": part_raw.zfill(3),
            }
    return None


def strip_split_video_suffix(filename: str) -> str:
    """Convert ``Movie.mkv.001`` to ``Movie.mkv``."""
    info = split_video_info(filename)
    return info["base_name"] if info else filename


def split_part_info(filename: str | None) -> Optional[Dict[str, Any]]:
    """Return split part info for supported split ZIP or direct video parts."""
    info = split_zip_info(filename)
    if info:
        info["kind"] = "split_zip"
        return info
    info = split_video_info(filename)
    if info:
        info["kind"] = "split_file"
        return info
    return None


def is_video_member(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


# Backwards-compatible names kept so older imports do not crash.  The new
# streaming path no longer uses disk cache, but other project code may still
# import these helpers from earlier builds.
def archive_cache_key(parts: Iterable[Dict[str, Any]], archive_name: str = "") -> str:
    import hashlib, json
    stable_parts = [
        {"chat_id": str(p.get("chat_id")), "msg_id": int(p.get("msg_id")), "part": int(p.get("part", 0))}
        for p in sorted(parts, key=lambda x: int(x.get("part", 0)))
    ]
    payload = json.dumps({"name": archive_name, "parts": stable_parts}, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def split_zip_cache_root() -> Path:
    root = Path("/tmp/tg_split_zip_cache")
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_cache_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._")
    return cleaned[:180] or "video.mkv"


def choose_video_member(zip_path: Path):
    with zipfile.ZipFile(zip_path, "r") as zf:
        candidates = [info for info in zf.infolist() if not info.is_dir() and is_video_member(info.filename)]
        if not candidates:
            raise ValueError("No playable video file found inside the ZIP archive")
        selected = max(candidates, key=lambda info: int(info.file_size or 0))
        return selected.filename, int(selected.file_size or 0)


def extract_member_to_file(zip_path: Path, member_name: str, target_path: Path) -> None:
    # Deprecated compatibility helper.
    import shutil
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open(member_name, "r") as src, open(tmp_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024 * 4)
    tmp_path.replace(target_path)


class TelegramSeekableReader:
    """Seekable file-like reader over one or more Telegram messages.

    The reader maps a logical byte range to the correct Telegram part and only
    keeps a small LRU cache of 1 MiB blocks in memory.  It intentionally avoids
    temporary files, making it suitable for Render/Hugging Face/Koyeb style
    hosts with small disks and RAM.
    """

    def __init__(
        self,
        client,
        messages: Union[Any, List[Any]],
        block_size: int = 1024 * 1024,
        max_cached_blocks: int = 8,
    ):
        self.client = client
        self.messages = messages if isinstance(messages, list) else [messages]
        self.block_size = int(block_size)
        self.max_cached_blocks = max(1, int(max_cached_blocks))
        self.parts: List[Dict[str, Any]] = []
        self.total_size = 0
        self.block_cache: "OrderedDict[tuple[int, int], bytes]" = OrderedDict()

        for msg in self.messages:
            media = getattr(msg, "video", None) or getattr(msg, "document", None) or getattr(msg, "audio", None)
            if not media:
                continue
            size = int(getattr(media, "file_size", 0) or 0)
            if size <= 0:
                continue
            self.parts.append({
                "message": msg,
                "media": media,
                "size": size,
                "start": self.total_size,
                "end": self.total_size + size,
            })
            self.total_size += size

    async def fetch_block(self, part_index: int, block_index: int) -> bytes:
        cache_key = (part_index, block_index)
        if cache_key in self.block_cache:
            self.block_cache.move_to_end(cache_key)
            return self.block_cache[cache_key]

        part = self.parts[part_index]
        message = part["message"]
        block = b""
        try:
            # PyroFork/Pyrogram stream_media offset is chunk-based.  One chunk is
            # normally 1 MiB, matching our block_size.
            async for chunk in self.client.stream_media(message, offset=int(block_index), limit=1):
                block = bytes(chunk or b"")
                break
        except TypeError:
            # Some forks expect the media object instead of the Message object.
            async for chunk in self.client.stream_media(part["media"], offset=int(block_index), limit=1):
                block = bytes(chunk or b"")
                break
        except Exception as exc:
            LOGGER.warning("Telegram block fetch failed part=%s block=%s: %s", part_index, block_index, exc)
            return b""

        if block:
            self.block_cache[cache_key] = block
            self.block_cache.move_to_end(cache_key)
            while len(self.block_cache) > self.max_cached_blocks:
                self.block_cache.popitem(last=False)
        return block

    async def read_range(self, start: int, end: int) -> bytes:
        if start >= self.total_size or start > end:
            return b""
        start = max(0, int(start))
        end = min(int(end), self.total_size - 1)

        out = bytearray()
        current = start
        remaining = end - start + 1

        while remaining > 0:
            part_idx = -1
            local_offset = -1
            for idx, part in enumerate(self.parts):
                if part["start"] <= current < part["end"]:
                    part_idx = idx
                    local_offset = current - part["start"]
                    break

            if part_idx < 0:
                break

            block_index = local_offset // self.block_size
            offset_in_block = local_offset % self.block_size
            block = await self.fetch_block(part_idx, block_index)
            if not block or offset_in_block >= len(block):
                break

            can_take = min(remaining, len(block) - offset_in_block)
            out.extend(block[offset_in_block: offset_in_block + can_take])
            current += can_take
            remaining -= can_take

        return bytes(out)


class SyncTelegramFile(io.RawIOBase):
    """Sync adapter so ``zipfile.ZipFile`` can read from TelegramSeekableReader."""

    def __init__(self, reader: TelegramSeekableReader, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.reader = reader
        self.loop = loop
        self.pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = int(offset)
        elif whence == io.SEEK_CUR:
            self.pos += int(offset)
        elif whence == io.SEEK_END:
            self.pos = self.reader.total_size + int(offset)
        self.pos = max(0, min(self.pos, self.reader.total_size))
        return self.pos

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.reader.total_size - self.pos
        if size <= 0 or self.pos >= self.reader.total_size:
            return b""
        fut = asyncio.run_coroutine_threadsafe(
            self.reader.read_range(self.pos, self.pos + int(size) - 1),
            self.loop,
        )
        data = fut.result()
        self.pos += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n


def list_zip_files_sync(reader: TelegramSeekableReader, loop: asyncio.AbstractEventLoop) -> list[zipfile.ZipInfo]:
    sync_file = SyncTelegramFile(reader, loop)
    try:
        with zipfile.ZipFile(sync_file) as zf:
            return zf.infolist()
    except Exception as exc:
        LOGGER.warning("Failed to list virtual ZIP: %s", exc)
        return []


async def list_zip_files(client, messages: Union[Any, List[Any]]) -> list[zipfile.ZipInfo]:
    reader = TelegramSeekableReader(client, messages)
    if reader.total_size < 4:
        return []
    first_block = await reader.fetch_block(0, 0)
    if not first_block.startswith(b"PK\x03\x04"):
        return []
    loop = asyncio.get_running_loop()
    return await anyio.to_thread.run_sync(list_zip_files_sync, reader, loop)


async def get_zip_entry_data_offset(reader: TelegramSeekableReader, header_offset: int) -> int:
    header = await reader.read_range(header_offset, header_offset + 29)
    if len(header) < 30 or not header.startswith(b"PK\x03\x04"):
        raise ValueError("Invalid ZIP local header")
    filename_len = int.from_bytes(header[26:28], "little")
    extra_len = int.from_bytes(header[28:30], "little")
    return int(header_offset) + 30 + filename_len + extra_len


async def zip_compressed_generator(
    reader: TelegramSeekableReader,
    entry_name: str,
    start: int,
    end: int,
    chunk_size: int = 1024 * 1024,
):
    """Yield a compressed ZIP member without extracting it to disk.

    Seeking in compressed ZIP entries is inherently slow because bytes must be
    decompressed from the beginning until ``start``.  This generator keeps RAM
    small but cannot make compressed ZIP seeking fast.
    """
    loop = asyncio.get_running_loop()
    send_stream, receive_stream = anyio.create_memory_object_stream(max_buffer_size=4)

    def thread_worker():
        try:
            sync_file = SyncTelegramFile(reader, loop)
            with zipfile.ZipFile(sync_file) as zf:
                with zf.open(entry_name) as src:
                    remaining_skip = max(0, int(start))
                    while remaining_skip > 0:
                        skipped = src.read(min(remaining_skip, chunk_size))
                        if not skipped:
                            break
                        remaining_skip -= len(skipped)

                    remaining = max(0, int(end) - int(start) + 1)
                    while remaining > 0:
                        data = src.read(min(remaining, chunk_size))
                        if not data:
                            break
                        asyncio.run_coroutine_threadsafe(send_stream.send(data), loop).result()
                        remaining -= len(data)
        except Exception as exc:
            LOGGER.warning("Compressed ZIP stream failed for %s: %s", entry_name, exc)
        finally:
            asyncio.run_coroutine_threadsafe(send_stream.aclose(), loop).result()

    Thread(target=thread_worker, daemon=True).start()

    async with receive_stream:
        async for chunk in receive_stream:
            yield chunk
