#!/usr/bin/env python3
"""Download document attachments from a set of Telegram channels.

The downloader is resumable: each channel gets a small JSON state file and a
separate directory. Files are written to ``.part`` paths and atomically renamed
only after Telegram reports a complete download.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp
from telethon import TelegramClient, errors, utils
from telethon.client.downloads import _CdnRedirect
from telethon.network import MTProtoSender
from telethon.tl import types
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InitConnectionRequest, InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import GetFileRequest


DEFAULT_CHANNELS = (
    "https://t.me/Badini_book",
    "https://t.me/pertok_badini",
    "https://t.me/jihana_pertuken_pdf",
)
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
CDN_FALLBACK_CHUNK_SIZE = 512 * 1024
# Telegram's hard maximum for one upload.getFile request. Offsets stay aligned
# to this so requests never cross a 1 MiB boundary (which would need `precise`).
PARALLEL_CHUNK_SIZE = 1024 * 1024
CHUNK_REQUEST_TIMEOUT = 120

URL_IN_TEXT = re.compile(r'https?://[^\s<>"\']+')
TRAILING_URL_PUNCTUATION = '.,;:!?…\'")»”]'
# Social/media platforms that never serve document files directly.
SKIP_LINK_DOMAINS = (
    "t.me", "telegram.me", "telegram.org", "telesco.pe",
    "youtube.com", "youtu.be", "instagram.com", "facebook.com", "fb.com",
    "twitter.com", "x.com", "tiktok.com", "wa.me", "whatsapp.com",
    "play.google.com", "apps.apple.com",
)
LINK_DOCUMENT_EXTENSIONS = {
    ".pdf", ".epub", ".djvu", ".doc", ".docx", ".mobi", ".azw3", ".rtf",
}
LINK_DOCUMENT_MIME_TYPES = {
    "application/pdf": ".pdf",
    "application/epub+zip": ".epub",
    "image/vnd.djvu": ".djvu",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/x-mobipocket-ebook": ".mobi",
}
LINK_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
# File hosts throttle per HTTP connection; files at least this large are
# split into parallel byte-range segments when the server supports ranges.
RANGE_SPLIT_MIN_SIZE = 8 * 1024 * 1024
RANGE_MAX_CONNECTIONS = 6


@dataclass
class ChannelStats:
    scanned: int = 0
    documents: int = 0
    downloaded: int = 0
    existing: int = 0
    failed: int = 0
    bytes_downloaded: int = 0
    links_found: int = 0
    links_downloaded: int = 0
    links_existing: int = 0
    links_unsupported: int = 0
    links_failed: int = 0
    link_bytes: int = 0


@dataclass
class DownloadJob:
    message: Any
    document_number: int
    target: Path
    expected_size: int
    document_date: str


@dataclass
class DownloadResult:
    job: DownloadJob
    size: int
    elapsed: float


@dataclass
class LinkJob:
    url: str
    message_id: int


@dataclass
class LinkResult:
    status: str  # done | skipped | unsupported | dead | failed
    filename: str | None = None
    size: int = 0
    detail: str = ""


class LinkTransferError(Exception):
    """A retryable HTTP transfer problem (short body, broken stream)."""


class LinkRangeUnsupported(Exception):
    """The server advertised byte ranges but did not honor them."""


def parse_args() -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Download all document attachments from Telegram channels."
    )
    parser.add_argument(
        "--channel",
        action="append",
        dest="channels",
        help="Telegram channel URL or username (repeatable). Defaults to the three configured channels.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_dir / "downloads",
        help="Root download directory (default: %(default)s)",
    )
    parser.add_argument(
        "--session",
        type=Path,
        default=project_dir / ".telegram" / "downloader",
        help="Telethon session path (default: %(default)s)",
    )
    parser.add_argument("--api-id", type=int, default=os.getenv("TELEGRAM_API_ID"))
    parser.add_argument("--api-hash", default=os.getenv("TELEGRAM_API_HASH"))
    parser.add_argument("--phone", default=os.getenv("TELEGRAM_PHONE"))
    parser.add_argument(
        "--force-rescan",
        action="store_true",
        help="Scan channel history from the beginning; already downloaded files are still skipped.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching documents without downloading or updating resume state.",
    )
    parser.add_argument(
        "--concurrent-downloads",
        type=int,
        default=4,
        metavar="N",
        help="Download N documents in parallel (default: %(default)s; maximum: 16).",
    )
    parser.add_argument(
        "--parallel-connections",
        type=int,
        default=8,
        metavar="N",
        help=(
            "Open N Telegram connections per data center and fetch each file "
            "in 1 MiB chunks spread across them (default: %(default)s; maximum: 16)."
        ),
    )
    parser.add_argument(
        "--download-retries",
        type=int,
        default=3,
        metavar="N",
        help="Retry each failed transfer N times (default: %(default)s; maximum: 10).",
    )
    return parser.parse_args()


def channel_slug(channel: str) -> str:
    value = channel.rstrip("/").rsplit("/", 1)[-1].lstrip("@")
    value = INVALID_FILENAME_CHARS.sub("_", value).strip(" .")
    return value or "channel"


def bounded_filename(name: str, max_bytes: int = 240) -> str:
    """Make a safe Linux/Windows filename no longer than max_bytes in UTF-8."""
    name = INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
    if not name:
        name = "document"
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name

    suffix = Path(name).suffix
    suffix_bytes = suffix.encode("utf-8")
    stem = name[: -len(suffix)] if suffix else name
    budget = max(1, max_bytes - len(suffix_bytes))
    short_stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return (short_stem.rstrip(" .") or "document") + suffix


def document_filename(message: Any) -> str:
    mime_type = getattr(message.file, "mime_type", None) or ""
    original = getattr(message.file, "name", None)
    if original:
        name = bounded_filename(original)
        if not Path(name).suffix:
            name += mimetypes.guess_extension(mime_type) or ""
        return name
    extension = mimetypes.guess_extension(mime_type) or ""
    return f"document_{message.id}{extension}"


def is_document_attachment(message: Any) -> bool:
    """Include generic documents, but not Telegram's audiovisual document types."""
    if not getattr(message, "document", None):
        return False
    excluded = ("video", "audio", "voice", "video_note", "gif", "sticker")
    return not any(getattr(message, attribute, None) for attribute in excluded)


def load_state(path: Path, channel: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "channel": channel,
            "last_scanned_message_id": 0,
            "documents": {},
            "links": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read resume state {path}: {exc}") from exc
    data.setdefault("last_scanned_message_id", 0)
    data.setdefault("documents", {})
    data.setdefault("links", {})
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def choose_target(directory: Path, filename: str, expected_size: int, message_id: int) -> tuple[Path, bool]:
    target = directory / filename
    if not target.exists():
        return target, False
    if target.is_file() and target.stat().st_size == expected_size:
        return target, True

    path = Path(filename)
    candidate = directory / f"{path.stem}__msg_{message_id}{path.suffix}"
    if not candidate.exists():
        return candidate, False
    if candidate.is_file() and candidate.stat().st_size == expected_size:
        return candidate, True
    return directory / f"{path.stem}__msg_{message_id}_{expected_size}{path.suffix}", False


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class StatusLine:
    """A replaceable terminal status line that degrades cleanly in logs."""

    def __init__(self) -> None:
        self.interactive = sys.stdout.isatty()
        self.visible = False

    def update(self, message: str) -> None:
        if not self.interactive:
            return
        width = max(20, shutil.get_terminal_size(fallback=(100, 24)).columns - 1)
        print(f"\r\033[2K{message[:width]}", end="", flush=True)
        self.visible = True

    def clear(self) -> None:
        if self.visible:
            print("\r\033[2K", end="", flush=True)
            self.visible = False


class BatchProgress:
    """Show combined throughput without concurrent progress lines fighting each other."""

    def __init__(self, status: StatusLine, jobs: list[DownloadJob]) -> None:
        self.status = status
        self.started_at = time.monotonic()
        self.current = {job.message.id: 0 for job in jobs}
        self.total = {job.message.id: job.expected_size for job in jobs}
        self.last_update = 0.0

    def callback(self, message_id: int):
        def update(current: int, total: int) -> None:
            self.current[message_id] = current
            if total:
                self.total[message_id] = total
            now = time.monotonic()
            if now - self.last_update < 0.25:
                return
            transferred = sum(self.current.values())
            expected = sum(self.total.values())
            elapsed = now - self.started_at
            speed = transferred / elapsed if elapsed > 0 else 0
            percent = transferred * 100 / expected if expected else 0
            self.status.update(
                f"  Downloading {len(self.current)} files... {percent:5.1f}%  "
                f"{format_bytes(transferred)} / {format_bytes(expected)}  "
                f"{format_bytes(int(speed))}/s"
            )
            self.last_update = now

        return update


class MediaSenderPool:
    """Extra MTProto connections to Telegram's media data centers.

    Telegram throttles throughput per TCP connection, so everything funneled
    through the client's single connection shares one narrow pipe no matter
    how many files are in flight. The pool keeps several authorized
    connections per DC and serves file chunks on whichever one is free.
    """

    def __init__(self, client: TelegramClient, size: int) -> None:
        self.client = client
        self.size = size
        self._pools: dict[int, asyncio.Queue[MTProtoSender]] = {}
        self._senders: list[MTProtoSender] = []
        self._lock = asyncio.Lock()

    async def _connect_sender(self, dc_id: int) -> MTProtoSender:
        client = self.client
        dc = await client._get_dc(dc_id)
        # The account's own DC accepts additional connections on the existing
        # auth key; other DCs need a freshly exported authorization.
        same_dc = dc_id == client.session.dc_id
        sender = MTProtoSender(
            client.session.auth_key if same_dc else None, loggers=client._log
        )
        await sender.connect(
            client._connection(
                dc.ip_address,
                dc.port,
                dc.id,
                loggers=client._log,
                proxy=client._proxy,
                local_addr=client._local_addr,
            )
        )
        if not same_dc:
            auth = await client(ExportAuthorizationRequest(dc_id=dc_id))
            source = client._init_request
            init = InitConnectionRequest(
                api_id=client.api_id,
                device_model=source.device_model,
                system_version=source.system_version,
                app_version=source.app_version,
                system_lang_code=source.system_lang_code,
                lang_pack=source.lang_pack,
                lang_code=source.lang_code,
                query=ImportAuthorizationRequest(id=auth.id, bytes=auth.bytes),
                proxy=source.proxy,
            )
            await sender.send(InvokeWithLayerRequest(LAYER, init))
        return sender

    async def _pool_for(self, dc_id: int) -> asyncio.Queue[MTProtoSender]:
        async with self._lock:
            queue = self._pools.get(dc_id)
            if queue is not None:
                return queue
            outcomes = await asyncio.gather(
                *(self._connect_sender(dc_id) for _ in range(self.size)),
                return_exceptions=True,
            )
            senders = [item for item in outcomes if isinstance(item, MTProtoSender)]
            if not senders:
                raise next(item for item in outcomes if isinstance(item, BaseException))
            queue = asyncio.Queue()
            for sender in senders:
                self._senders.append(sender)
                queue.put_nowait(sender)
            self._pools[dc_id] = queue
            return queue

    async def fetch(self, dc_id: int, location: Any, offset: int, limit: int) -> bytes:
        queue = await self._pool_for(dc_id)
        sender = await queue.get()
        try:
            while True:
                request = GetFileRequest(location, offset=offset, limit=limit)
                try:
                    result = await asyncio.wait_for(
                        sender.send(request), timeout=CHUNK_REQUEST_TIMEOUT
                    )
                except errors.FloodWaitError as exc:
                    if exc.seconds > 60:
                        raise
                    await asyncio.sleep(exc.seconds + 1)
                    continue
                if isinstance(result, types.upload.FileCdnRedirect):
                    raise _CdnRedirect(result)
                return result.bytes
        finally:
            queue.put_nowait(sender)

    async def close(self) -> None:
        await asyncio.gather(
            *(sender.disconnect() for sender in self._senders),
            return_exceptions=True,
        )


async def download_parallel(
    pool: MediaSenderPool,
    job: DownloadJob,
    partial: Path,
    start_offset: int,
    progress_update,
) -> None:
    """Fill ``partial`` from ``start_offset`` using parallel chunk requests.

    On failure the file is truncated back to the longest contiguous prefix so
    the plain size-based resume logic stays correct despite out-of-order writes.
    """
    dc_id, location = utils.get_input_location(job.message.document)
    offsets = list(range(start_offset, job.expected_size, PARALLEL_CHUNK_SIZE))
    completed: set[int] = set()
    transferred = start_offset
    next_index = 0

    with partial.open("r+b" if partial.exists() else "w+b") as output:
        output.truncate(start_offset)
        fd = output.fileno()

        async def worker() -> None:
            nonlocal next_index, transferred
            while next_index < len(offsets):
                offset = offsets[next_index]
                next_index += 1
                chunk = await pool.fetch(dc_id, location, offset, PARALLEL_CHUNK_SIZE)
                expected = min(PARALLEL_CHUNK_SIZE, job.expected_size - offset)
                if len(chunk) != expected:
                    raise RuntimeError(
                        f"short read at offset {offset}: "
                        f"received {len(chunk)}, expected {expected}"
                    )
                os.pwrite(fd, chunk, offset)
                completed.add(offset)
                transferred += len(chunk)
                progress_update(transferred, job.expected_size)

        workers = [
            asyncio.create_task(worker())
            for _ in range(max(1, min(pool.size, len(offsets))))
        ]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            resume = start_offset
            while resume in completed:
                resume += PARALLEL_CHUNK_SIZE
            os.ftruncate(fd, min(resume, job.expected_size))
            output.flush()
            os.fsync(fd)
            raise
        output.flush()
        os.fsync(fd)


async def download_one(
    client: TelegramClient,
    pool: MediaSenderPool,
    job: DownloadJob,
    progress: BatchProgress,
    download_retries: int,
) -> DownloadResult:
    partial = job.target.with_name(job.target.name + ".part")
    started_at = time.monotonic()
    for attempt in range(download_retries + 1):
        try:
            input_chat = getattr(job.message, "input_chat", None)
            msg_data = (input_chat, job.message.id) if input_chat else None

            # Resume at a chunk boundary so every request stays 1 MiB-aligned.
            # A partial final chunk is truncated and requested again.
            offset = partial.stat().st_size if partial.exists() else 0
            if job.expected_size and offset > job.expected_size:
                offset = 0
            offset -= offset % PARALLEL_CHUNK_SIZE
            if partial.exists() and partial.stat().st_size != offset:
                with partial.open("r+b") as output:
                    output.truncate(offset)

            if not job.expected_size or offset < job.expected_size:
                progress.callback(job.message.id)(offset, job.expected_size)
                try:
                    if job.expected_size:
                        await download_parallel(
                            pool, job, partial, offset,
                            progress.callback(job.message.id),
                        )
                    else:
                        # Unknown size: let Telethon stream it sequentially.
                        await client._download_file(
                            job.message.document,
                            str(partial),
                            part_size_kb=CDN_FALLBACK_CHUNK_SIZE // 1024,
                            file_size=job.expected_size,
                            progress_callback=progress.callback(job.message.id),
                            msg_data=msg_data,
                        )
                except _CdnRedirect:
                    # CDN downloads require offset-aware decryption. Delegate
                    # those uncommon transfers to Telethon's complete handler.
                    await client._download_file(
                        job.message.document,
                        str(partial),
                        part_size_kb=CDN_FALLBACK_CHUNK_SIZE // 1024,
                        file_size=job.expected_size,
                        progress_callback=progress.callback(job.message.id),
                        msg_data=msg_data,
                    )
            if not partial.exists():
                raise RuntimeError("Telegram returned no downloaded file")
            size = partial.stat().st_size
            if job.expected_size and size != job.expected_size:
                raise RuntimeError(
                    f"size mismatch: expected {job.expected_size}, received {size}"
                )
            os.replace(partial, job.target)
            return DownloadResult(job, size, time.monotonic() - started_at)
        except asyncio.CancelledError:
            raise
        except errors.FloodWaitError:
            # The client already sleeps for short waits (configured at 60s).
            # Longer limits should reach the channel-level handler so the user
            # sees Telegram's actual retry-after time instead of rapid retries.
            raise
        except Exception as exc:
            if attempt >= download_retries:
                raise
            # Raw upload.getFile calls bypass Telethon's automatic refetch of
            # expired file references, so refresh the message before retrying.
            if isinstance(exc, errors.FileReferenceExpiredError) and input_chat:
                fresh = await client.get_messages(input_chat, ids=job.message.id)
                if fresh is not None and getattr(fresh, "document", None):
                    job.message = fresh
            delay = min(8, 2**attempt)
            progress.status.clear()
            print(
                f"    ↻ retry {attempt + 1}/{download_retries} for "
                f"{job.target.name} in {delay}s: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)

    raise AssertionError("unreachable")


async def download_batch(
    client: TelegramClient,
    pool: MediaSenderPool,
    jobs: list[DownloadJob],
    status: StatusLine,
    download_retries: int,
) -> tuple[list[DownloadResult], list[BaseException]]:
    status.clear()
    label = f"{len(jobs)} files concurrently" if len(jobs) != 1 else "1 file"
    print(f"  Downloading {label}...")
    progress = BatchProgress(status, jobs)
    outcomes = await asyncio.gather(
        *(download_one(client, pool, job, progress, download_retries) for job in jobs),
        return_exceptions=True,
    )
    status.clear()
    results: list[DownloadResult] = []
    failures: list[BaseException] = []
    for job, outcome in zip(jobs, outcomes):
        if isinstance(outcome, BaseException):
            failures.append(outcome)
            print(
                f"    ✗ {job.target.name}: {type(outcome).__name__}: {outcome}",
                file=sys.stderr,
            )
        else:
            results.append(outcome)
            average = outcome.size / outcome.elapsed if outcome.elapsed > 0 else 0
            print(
                f"    ✓ {job.target.name} — {format_bytes(outcome.size)} in "
                f"{format_duration(outcome.elapsed)} ({format_bytes(int(average))}/s)"
            )
    return results, failures


def extract_candidate_urls(message: Any) -> list[str]:
    """Collect unique external http(s) links worth probing for documents."""
    candidates: list[str] = []
    text = message.message or ""
    candidates.extend(URL_IN_TEXT.findall(text))
    for entity in message.entities or []:
        if isinstance(entity, types.MessageEntityTextUrl) and entity.url:
            candidates.append(entity.url)
    preview = getattr(message, "web_preview", None)
    preview_url = getattr(preview, "url", None)
    if preview_url:
        candidates.append(preview_url)
    # When the link preview embeds the document itself, Telethon exposes it as
    # message.document and it downloads through Telegram already; probing the
    # same URL over HTTP would duplicate the file.
    exclude = {preview_url} if preview_url and message.document is not None else set()

    unique: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        url = url.rstrip(TRAILING_URL_PUNCTUATION)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        host = parsed.netloc.lower().split(":")[0]
        if any(host == domain or host.endswith("." + domain) for domain in SKIP_LINK_DOMAINS):
            continue
        if url in seen or url in exclude:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def parse_google_drive(url: str) -> tuple[str, str] | None:
    host = urlparse(url).netloc.lower()
    if not host.endswith(
        ("drive.google.com", "docs.google.com", "drive.usercontent.google.com")
    ):
        return None
    match = re.search(r"/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)", url)
    if match:
        return "folder", match.group(1)
    match = re.search(r"/(?:file|document)/d/([A-Za-z0-9_-]+)", url)
    if match:
        return ("gdoc" if "/document/d/" in url else "file"), match.group(1)
    match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", url)
    if match:
        return "file", match.group(1)
    return "unknown", ""


class LinkProgress:
    """Combined status line for concurrent HTTP downloads."""

    def __init__(self, status: StatusLine, count: int) -> None:
        self.status = status
        self.count = count
        self.received: dict[str, int] = {}
        self.started_at = time.monotonic()
        self.last_update = 0.0

    def update(self, url: str, received: int) -> None:
        self.received[url] = received
        now = time.monotonic()
        if now - self.last_update < 0.25:
            return
        transferred = sum(self.received.values())
        elapsed = now - self.started_at
        speed = transferred / elapsed if elapsed > 0 else 0
        self.status.update(
            f"  Fetching {self.count} linked files... "
            f"{format_bytes(transferred)}  {format_bytes(int(speed))}/s"
        )
        self.last_update = now


async def download_link_ranges(
    http: aiohttp.ClientSession,
    url: str,
    job: LinkJob,
    target: Path,
    expected: int,
    progress: LinkProgress,
) -> LinkResult:
    """Fetch one file over several parallel byte-range connections."""
    partial = target.with_name(target.name + ".part")
    connections = min(RANGE_MAX_CONNECTIONS, max(2, expected // RANGE_SPLIT_MIN_SIZE + 1))
    bounds = [expected * i // connections for i in range(connections + 1)]
    received_total = 0

    try:
        with partial.open("w+b") as output:
            output.truncate(expected)
            fd = output.fileno()

            async def fetch_segment(start: int, end: int) -> None:
                nonlocal received_total
                headers = {"Range": f"bytes={start}-{end - 1}"}
                async with http.get(url, headers=headers) as response:
                    if response.status != 206:
                        raise LinkRangeUnsupported(f"HTTP {response.status} for range request")
                    offset = start
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        os.pwrite(fd, chunk, offset)
                        offset += len(chunk)
                        received_total += len(chunk)
                        progress.update(job.url, received_total)
                    if offset != end:
                        raise LinkTransferError(
                            f"segment ended at {offset}, expected {end}"
                        )

            tasks = [
                asyncio.create_task(fetch_segment(bounds[i], bounds[i + 1]))
                for i in range(connections)
            ]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            output.flush()
            os.fsync(fd)
    except LinkRangeUnsupported:
        # The server lied about range support; stream on one connection.
        partial.unlink(missing_ok=True)
        received = 0
        try:
            async with http.get(url) as response:
                response.raise_for_status()
                with partial.open("wb") as output:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        output.write(chunk)
                        received += len(chunk)
                        progress.update(job.url, received)
                    output.flush()
                    os.fsync(output.fileno())
            if received != expected:
                raise LinkTransferError(f"incomplete body: {received} of {expected} bytes")
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        os.replace(partial, target)
        return LinkResult("done", target.name, received)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    os.replace(partial, target)
    return LinkResult("done", target.name, expected)


async def stream_link_response(
    http: aiohttp.ClientSession,
    response: aiohttp.ClientResponse,
    job: LinkJob,
    directory: Path,
    progress: LinkProgress,
) -> LinkResult:
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    disposition = response.content_disposition
    name = disposition.filename if disposition and disposition.filename else ""
    if not name:
        name = unquote(Path(urlparse(str(response.url)).path).name)
    if Path(name).suffix.lower() not in LINK_DOCUMENT_EXTENSIONS:
        mapped = LINK_DOCUMENT_MIME_TYPES.get(content_type)
        if not mapped:
            return LinkResult(
                "unsupported", detail=f"not a document ({content_type or 'unknown type'})"
            )
        name = (name or f"link_{hashlib.sha1(job.url.encode()).hexdigest()[:10]}") + mapped

    filename = bounded_filename(name)
    expected = int(response.headers.get("Content-Length") or 0)
    if (response.headers.get("Content-Encoding") or "identity").lower() not in ("", "identity"):
        expected = 0  # aiohttp decompresses; the header length is the wire size
    target = directory / filename
    if target.exists():
        if not expected or target.stat().st_size == expected:
            return LinkResult("skipped", target.name, expected)
        digest = hashlib.sha1(job.url.encode()).hexdigest()[:8]
        target = directory / f"{Path(filename).stem}__link_{digest}{Path(filename).suffix}"
        if target.exists() and (not expected or target.stat().st_size == expected):
            return LinkResult("skipped", target.name, expected)

    # Sniff the body before writing: hosts routinely serve HTML error or
    # landing pages with 200 status under document-looking URLs.
    iterator = response.content.iter_chunked(256 * 1024)
    first = b""
    async for chunk in iterator:
        first = chunk
        break
    if not first:
        return LinkResult("unsupported", detail="empty response")
    probe = first[:1024].lstrip().lower()
    if probe.startswith((b"<!doctype", b"<html", b"<head", b"<body", b"<meta", b"<script")):
        return LinkResult("unsupported", detail="link serves a web page, not a file")
    if target.suffix.lower() == ".pdf" and b"%pdf-" not in first[:1024].lower():
        return LinkResult("unsupported", detail="response is not a PDF")

    if (
        expected >= RANGE_SPLIT_MIN_SIZE
        and (response.headers.get("Accept-Ranges") or "").lower() == "bytes"
    ):
        # Re-request as parallel segments; the few KiB already read are cheap
        # compared to a single throttled connection for a large file.
        final_url = str(response.url)
        response.close()
        return await download_link_ranges(http, final_url, job, target, expected, progress)

    # HTTP retries restart from scratch, so a failed transfer must not leave
    # a stale .part file behind.
    partial = target.with_name(target.name + ".part")
    received = 0
    try:
        with partial.open("wb") as output:
            output.write(first)
            received += len(first)
            progress.update(job.url, received)
            async for chunk in iterator:
                output.write(chunk)
                received += len(chunk)
                progress.update(job.url, received)
            output.flush()
            os.fsync(output.fileno())
        if expected and received != expected:
            raise LinkTransferError(f"incomplete body: {received} of {expected} bytes")
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, target)
    return LinkResult("done", target.name, received)


async def fetch_google_drive_file(
    http: aiohttp.ClientSession,
    job: LinkJob,
    file_id: str,
    directory: Path,
    progress: LinkProgress,
) -> LinkResult:
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    async with http.get(url) as response:
        if response.status in (404, 410):
            return LinkResult("dead", detail=f"HTTP {response.status}")
        response.raise_for_status()
        if "text/html" not in (response.headers.get("Content-Type") or "").lower():
            return await stream_link_response(http, response, job, directory, progress)
        page = await response.text()

    lowered = page.lower()
    if ("quota" in lowered and "exceeded" in lowered) or "too many users" in lowered:
        return LinkResult(
            "failed", detail="Google Drive quota exceeded (will retry on a later run)"
        )
    # Large public files return an interstitial "can't scan for viruses" form;
    # replaying its hidden fields against the form action yields the file.
    action = re.search(r'action="([^"]*usercontent\.google\.com/download[^"]*)"', page)
    if not action:
        if "request access" in lowered or "you need access" in lowered:
            return LinkResult("unsupported", detail="Google Drive file is not public")
        return LinkResult("unsupported", detail="Google Drive did not offer a download")
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))
    async with http.get(html.unescape(action.group(1)), params=fields) as response:
        response.raise_for_status()
        if "text/html" in (response.headers.get("Content-Type") or "").lower():
            return LinkResult("failed", detail="Google Drive returned another interstitial page")
        return await stream_link_response(http, response, job, directory, progress)


async def fetch_mediafire_file(
    http: aiohttp.ClientSession,
    job: LinkJob,
    directory: Path,
    progress: LinkProgress,
) -> LinkResult:
    """MediaFire file URLs land on an HTML page; the real file sits behind
    its download button."""
    async with http.get(job.url) as response:
        if response.status in (404, 410):
            return LinkResult("dead", detail=f"HTTP {response.status}")
        response.raise_for_status()
        if "text/html" not in (response.headers.get("Content-Type") or "").lower():
            return await stream_link_response(http, response, job, directory, progress)
        page = await response.text()

    match = re.search(r'href="(https?://download[^"]+)"', page)
    if not match:
        lowered = page.lower()
        if "file has been removed" in lowered or "dangerous file blocked" in lowered:
            return LinkResult("dead", detail="MediaFire file removed")
        return LinkResult("unsupported", detail="MediaFire did not offer a direct download")
    async with http.get(html.unescape(match.group(1))) as response:
        response.raise_for_status()
        return await stream_link_response(http, response, job, directory, progress)


async def fetch_link(
    http: aiohttp.ClientSession,
    job: LinkJob,
    directory: Path,
    progress: LinkProgress,
) -> LinkResult:
    url = job.url
    host = urlparse(url).netloc.lower()
    if host.endswith("mediafire.com"):
        return await fetch_mediafire_file(http, job, directory, progress)
    drive = parse_google_drive(url)
    if drive:
        kind, file_id = drive
        if kind == "folder":
            return LinkResult(
                "unsupported", detail="Google Drive folder (fetch its files individually)"
            )
        if kind == "unknown":
            return LinkResult("unsupported", detail="unrecognized Google link")
        if kind == "file":
            return await fetch_google_drive_file(http, job, file_id, directory, progress)
        url = f"https://docs.google.com/document/d/{file_id}/export?format=pdf"
    elif "dropbox.com" in urlparse(url).netloc.lower():
        url = re.sub(r"([?&])dl=0\b", r"\g<1>dl=1", url)
        if "dl=1" not in url:
            url += ("&" if "?" in url else "?") + "dl=1"

    async with http.get(url) as response:
        if response.status in (404, 410):
            return LinkResult("dead", detail=f"HTTP {response.status}")
        response.raise_for_status()
        return await stream_link_response(http, response, job, directory, progress)


async def download_link_one(
    http: aiohttp.ClientSession,
    job: LinkJob,
    directory: Path,
    download_retries: int,
    progress: LinkProgress,
) -> LinkResult:
    last_error = "unknown error"
    for attempt in range(download_retries + 1):
        try:
            return await fetch_link(http, job, directory, progress)
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientResponseError as exc:
            if exc.status in (404, 410):
                return LinkResult("dead", detail=f"HTTP {exc.status}")
            if 400 <= exc.status < 500 and exc.status != 429:
                return LinkResult("failed", detail=f"HTTP {exc.status}")
            last_error = f"HTTP {exc.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError, LinkTransferError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < download_retries:
            await asyncio.sleep(min(8, 2**attempt))
    return LinkResult("failed", detail=last_error)


async def download_channel(
    client: TelegramClient,
    pool: MediaSenderPool,
    http: aiohttp.ClientSession,
    channel: str,
    output_root: Path,
    force_rescan: bool,
    dry_run: bool,
    concurrent_downloads: int,
    download_retries: int,
) -> ChannelStats:
    channel_started_at = time.monotonic()
    slug = channel_slug(channel)
    directory = output_root / slug
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / ".download_state.json"
    state = load_state(state_path, channel)
    min_id = 0 if force_rescan else int(state["last_scanned_message_id"])
    # States written before link support have scanned past messages whose
    # links were never collected; backfill them once with a full rescan.
    link_backfill = (
        not force_rescan and not dry_run and min_id > 0 and not state.get("link_scan_done")
    )
    if link_backfill:
        min_id = 0
    stats = ChannelStats()
    status = StatusLine()

    print(f"\n┌─ Channel: {channel}")
    print(f"├─ Destination: {directory}")
    if force_rescan:
        print("└─ Scan mode: full history (existing files will be skipped)")
    elif link_backfill:
        print(
            "└─ Scan mode: one-time full rescan to collect posted links "
            "(existing files will be skipped)"
        )
    elif min_id:
        print(f"└─ Scan mode: resuming after message #{min_id}")
    else:
        print("└─ Scan mode: full history (no checkpoint found)")

    status.update(f"  Resolving {channel} ...")
    entity = await client.get_entity(channel)
    state_dirty = False
    last_scan_update = 0.0
    pending: list[DownloadJob] = []
    pending_links: list[LinkJob] = []
    last_seen_message_id = min_id

    if not dry_run:
        # Links recorded but not finished on an earlier run (pending) or that
        # failed transiently are retried before scanning new messages.
        for url, entry in state["links"].items():
            if entry.get("status") in ("pending", "failed"):
                pending_links.append(LinkJob(url, int(entry.get("message_id") or 0)))
        if pending_links:
            print(f"  Retrying {len(pending_links)} link(s) from previous runs.")

    async def flush_links() -> None:
        nonlocal pending_links
        if not pending_links:
            return
        status.clear()
        print(f"  Fetching {len(pending_links)} linked file(s)...")
        progress = LinkProgress(status, len(pending_links))
        semaphore = asyncio.Semaphore(concurrent_downloads)

        async def run(job: LinkJob) -> LinkResult:
            async with semaphore:
                return await download_link_one(
                    http, job, directory, download_retries, progress
                )

        outcomes = await asyncio.gather(
            *(run(job) for job in pending_links), return_exceptions=True
        )
        status.clear()
        for job, outcome in zip(pending_links, outcomes):
            if isinstance(outcome, BaseException):
                outcome = LinkResult(
                    "failed", detail=f"{type(outcome).__name__}: {outcome}"
                )
            entry = state["links"].setdefault(job.url, {"message_id": job.message_id})
            entry["status"] = outcome.status
            entry["detail"] = outcome.detail
            if outcome.filename:
                entry["filename"] = outcome.filename
            if outcome.status == "done":
                stats.links_downloaded += 1
                stats.link_bytes += outcome.size
                print(f"    ✓ link: {outcome.filename} — {format_bytes(outcome.size)}")
            elif outcome.status == "skipped":
                stats.links_existing += 1
                print(f"    • link already present: {outcome.filename}")
            elif outcome.status in ("unsupported", "dead"):
                stats.links_unsupported += 1
                print(f"    – link skipped ({outcome.detail}): {job.url}")
            else:
                stats.links_failed += 1
                print(
                    f"    ✗ link failed ({outcome.detail}): {job.url}",
                    file=sys.stderr,
                )
        save_state(state_path, state)
        pending_links = []

    async def flush_pending() -> None:
        nonlocal pending, state_dirty
        if not pending:
            return
        results, failures = await download_batch(
            client, pool, pending, status, download_retries
        )
        stats.downloaded += len(results)
        stats.bytes_downloaded += sum(result.size for result in results)
        stats.failed += len(failures)
        for result in results:
            job = result.job
            state["documents"][str(job.message.id)] = {
                "filename": job.target.name,
                "size": job.expected_size,
                "message_date": job.message.date.isoformat() if job.message.date else None,
            }
        if failures:
            # Do not advance the scan checkpoint past a failed transfer. Successful
            # files in this batch will be detected by size on the next run.
            raise failures[0]
        state["last_scanned_message_id"] = last_seen_message_id
        save_state(state_path, state)
        state_dirty = False
        pending = []

    async for message in client.iter_messages(entity, reverse=True, min_id=min_id):
        stats.scanned += 1
        last_seen_message_id = message.id
        now = time.monotonic()
        if now - last_scan_update >= 0.25:
            status.update(
                f"  Scanning... {stats.scanned} messages checked, "
                f"{stats.documents} documents found"
            )
            last_scan_update = now

        for url in extract_candidate_urls(message):
            stats.links_found += 1
            if dry_run:
                status.clear()
                print(f"  [link] would fetch: {url}")
            elif url not in state["links"]:
                # Recorded before the checkpoint can pass this message, so a
                # crash before the fetch cannot lose the link.
                state["links"][url] = {"status": "pending", "message_id": message.id}
                state_dirty = True
                pending_links.append(LinkJob(url, message.id))
        if len(pending_links) >= max(8, concurrent_downloads):
            await flush_links()

        if not is_document_attachment(message):
            if not dry_run:
                state_dirty = True
                if not pending:
                    state["last_scanned_message_id"] = message.id
                    # Limit disk churn on channels containing many text/photo posts.
                    if stats.scanned % 100 == 0:
                        save_state(state_path, state)
                        state_dirty = False
            continue

        stats.documents += 1
        filename = document_filename(message)
        expected_size = int(getattr(message.file, "size", 0) or 0)
        target, already_exists = choose_target(directory, filename, expected_size, message.id)

        status.clear()
        document_date = message.date.astimezone().strftime("%Y-%m-%d") if message.date else "unknown date"
        if dry_run:
            print(
                f"  [document {stats.documents}] would download: {filename}\n"
                f"    message #{message.id} • {document_date} • {format_bytes(expected_size)}"
            )
            continue

        if already_exists:
            print(
                f"  [document {stats.documents}] already present: {target.name}\n"
                f"    message #{message.id} • {document_date} • {format_bytes(expected_size)}"
            )
            stats.existing += 1
            state["documents"][str(message.id)] = {
                "filename": target.name,
                "size": expected_size,
                "message_date": message.date.isoformat() if message.date else None,
            }
            state_dirty = True
        else:
            print(
                f"  [document {stats.documents}] queued: {target.name}\n"
                f"    message #{message.id} • {document_date} • {format_bytes(expected_size)}"
            )
            pending.append(
                DownloadJob(message, stats.documents, target, expected_size, document_date)
            )
            if len(pending) >= concurrent_downloads:
                await flush_pending()

        if not pending:
            state["last_scanned_message_id"] = message.id
            save_state(state_path, state)
            state_dirty = False

        status.update(
            f"  Scanning... {stats.scanned} messages checked, "
            f"{stats.documents} documents found"
        )

    # Links first: their outcomes persist in state even if a document batch
    # failure below aborts the channel.
    await flush_links()
    await flush_pending()
    status.clear()
    if not dry_run and not state.get("link_scan_done"):
        state["link_scan_done"] = True
        state_dirty = True
    if state_dirty:
        state["last_scanned_message_id"] = last_seen_message_id
        save_state(state_path, state)
    print(
        f"  Channel complete in {format_duration(time.monotonic() - channel_started_at)}: "
        f"{stats.scanned} messages scanned, {stats.documents} documents, "
        f"{stats.downloaded} downloaded, {stats.existing} already present, "
        f"{stats.links_found} links seen, {stats.links_downloaded} linked files fetched."
    )
    return stats


async def async_main(args: argparse.Namespace) -> int:
    if not 1 <= args.concurrent_downloads <= 16:
        print("--concurrent-downloads must be between 1 and 16.", file=sys.stderr)
        return 2
    if not 1 <= args.parallel_connections <= 16:
        print("--parallel-connections must be between 1 and 16.", file=sys.stderr)
        return 2
    if not 0 <= args.download_retries <= 10:
        print("--download-retries must be between 0 and 10.", file=sys.stderr)
        return 2
    if not args.api_id or not args.api_hash:
        print(
            "Missing Telegram API credentials. Set TELEGRAM_API_ID and TELEGRAM_API_HASH "
            "from https://my.telegram.org/apps (or pass --api-id and --api-hash).",
            file=sys.stderr,
        )
        return 2

    args.output = args.output.expanduser().resolve()
    args.session = args.session.expanduser().resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    args.session.parent.mkdir(parents=True, exist_ok=True)
    channels = args.channels or list(DEFAULT_CHANNELS)
    run_started_at = time.monotonic()

    print("Telegram document downloader")
    print(f"  Channels: {len(channels)}")
    print(f"  Output:   {args.output}")
    print(f"  Mode:     {'preview only' if args.dry_run else 'download and resume'}")
    if not args.dry_run:
        print(
            f"  Parallel: {args.concurrent_downloads} simultaneous downloads "
            f"over {args.parallel_connections} connections per data center"
        )
        print(f"  Retries:  {args.download_retries} per file")
    print("\nConnecting to Telegram...", flush=True)

    client = TelegramClient(
        str(args.session),
        int(args.api_id),
        args.api_hash,
        request_retries=5,
        connection_retries=5,
        auto_reconnect=True,
        flood_sleep_threshold=60,
    )

    pool = MediaSenderPool(client, args.parallel_connections)
    try:
        await client.start(phone=args.phone)
        me = await client.get_me()
        account_name = " ".join(
            part for part in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if part
        )
        username = getattr(me, "username", None)
        account = account_name or (f"@{username}" if username else "Telegram account")
        if username and account_name:
            account += f" (@{username})"
        print(f"✓ Connected as {account}")
        failed_channels = 0
        totals = ChannelStats()
        http_timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=120)
        async with aiohttp.ClientSession(
            timeout=http_timeout, headers={"User-Agent": LINK_USER_AGENT}
        ) as http:
            for channel in channels:
                try:
                    stats = await download_channel(
                        client,
                        pool,
                        http,
                        channel,
                        args.output,
                        args.force_rescan,
                        args.dry_run,
                        args.concurrent_downloads,
                        args.download_retries,
                    )
                except errors.FloodWaitError as exc:
                    failed_channels += 1
                    print(
                        f"  ✗ Telegram rate limit: retry this channel in "
                        f"{format_duration(exc.seconds)}.",
                        file=sys.stderr,
                    )
                    continue
                except Exception as exc:
                    failed_channels += 1
                    print(f"  ✗ Failed {channel}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    continue

                for field in stats.__dataclass_fields__:
                    setattr(totals, field, getattr(totals, field) + getattr(stats, field))
        print("\nRun summary")
        print(f"  Messages scanned:  {totals.scanned}")
        print(f"  Documents found:   {totals.documents}")
        print(f"  Files downloaded:  {totals.downloaded} ({format_bytes(totals.bytes_downloaded)})")
        print(f"  Already present:   {totals.existing}")
        print(
            f"  Linked files:      {totals.links_downloaded} downloaded "
            f"({format_bytes(totals.link_bytes)}), {totals.links_existing} already present, "
            f"{totals.links_unsupported} not downloadable, {totals.links_failed} failed"
        )
        print(f"  Channels failed:   {failed_channels}")
        print(f"  Total elapsed:     {format_duration(time.monotonic() - run_started_at)}")
        return 1 if failed_channels else 0
    finally:
        await pool.close()
        await client.disconnect()


def main() -> int:
    try:
        return asyncio.run(async_main(parse_args()))
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
