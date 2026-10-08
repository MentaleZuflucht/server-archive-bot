"""Downloads attachments from the monitored channels and records them in the database."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

import aiohttp
import discord

from database import AttachmentInfo, Database

logger = logging.getLogger('bot.archiver')

DOWNLOAD_ATTEMPTS = 3
CHUNK_SIZE = 64 * 1024
# The CDN answers these for deleted files and expired links; retrying will not help.
GONE_STATUSES = {403, 404, 410}
# During history scans, save the scan position this often (in messages).
PROGRESS_EVERY = 100
SCAN_ATTEMPTS = 3
MAX_NAME_LENGTH = 100
# Characters that are not allowed in Windows file names, plus control characters.
UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class DownloadGone(Exception):
    """The file is no longer on Discord's CDN."""


class Archiver:
    def __init__(
        self,
        client: discord.Client,
        db: Database,
        archive_dir: Path,
        channel_ids: frozenset[int],
    ) -> None:
        self.client = client
        self.db = db
        self.archive_dir = archive_dir
        self.channel_ids = channel_ids
        self.session: aiohttp.ClientSession | None = None
        # Attachments being downloaded right now, so a live message and the
        # history scan never write the same file at the same time.
        self._in_flight: set[int] = set()

    async def start(self) -> None:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        # No total timeout: large videos can take a while. A stalled read still times out.
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()

    def is_monitored(self, channel: discord.abc.MessageableChannel) -> bool:
        """True for monitored channels and threads (or forum posts) inside them."""
        parent_id = getattr(channel, 'parent_id', None)
        return channel.id in self.channel_ids or parent_id in self.channel_ids

    async def archive_message(self, message: discord.Message) -> int:
        """Archive every attachment of a message. Returns how many files were downloaded."""
        downloaded = 0
        for attachment in _attachments(message):
            if await self._archive_attachment(attachment, message):
                downloaded += 1
        return downloaded

    async def retry_failed_downloads(self) -> None:
        """Retry downloads that failed before, using freshly signed URLs."""
        pending = await self.db.pending_downloads()
        if not pending:
            return
        logger.info('Retrying %s download(s) that failed earlier', len(pending))

        by_message: dict[tuple[int, int], set[int]] = defaultdict(set)
        for row in pending:
            by_message[(row.channel_id, row.message_id)].add(row.id)

        for (channel_id, message_id), attachment_ids in by_message.items():
            try:
                channel = await self._get_channel(channel_id)
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                logger.warning(
                    'Message %s in channel %s was deleted; %s attachment(s) cannot be downloaded',
                    message_id, channel_id, len(attachment_ids),
                )
                for attachment_id in attachment_ids:
                    await self.db.mark_unavailable(attachment_id)
                continue
            except discord.HTTPException as exc:
                logger.warning('Could not fetch message %s to retry it: %s', message_id, exc)
                continue

            for attachment in _attachments(message):
                if attachment.id in attachment_ids:
                    attachment_ids.discard(attachment.id)
                    await self._archive_attachment(attachment, message)
            # Attachments that were removed from the message by an edit.
            for attachment_id in attachment_ids:
                await self.db.mark_unavailable(attachment_id)

    async def archive_history(self) -> None:
        """Scan every monitored channel, its threads and forum posts for attachments.

        Each channel resumes after the newest message the last scan finished, so
        only the first scan has to go through the whole history.
        """
        started = time.monotonic()
        downloaded = 0
        for channel_id in sorted(self.channel_ids):
            try:
                channel = await self._get_channel(channel_id)
            except discord.NotFound:
                logger.warning('Channel %s does not exist', channel_id)
                continue
            except discord.HTTPException as exc:
                logger.warning('Cannot open channel %s: %s', channel_id, exc)
                continue
            logger.info('Scanning #%s (%s)', channel.name, channel_id)
            downloaded += await self._scan_channel(channel)

        elapsed = time.monotonic() - started
        logger.info('History scan done: %s new file(s) in %.0fs', downloaded, elapsed)

    async def _scan_channel(self, channel: discord.abc.GuildChannel) -> int:
        downloaded = 0
        # Forum and media channels only hold threads, no messages of their own.
        if isinstance(channel, discord.abc.Messageable):
            downloaded += await self._scan_messages(channel)

        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            return downloaded

        threads = {thread.id: thread for thread in channel.threads}
        archived = [channel.archived_threads(limit=None)]
        if isinstance(channel, discord.TextChannel):
            archived.append(channel.archived_threads(limit=None, private=True))
        for iterator in archived:
            try:
                async for thread in iterator:
                    threads[thread.id] = thread
            except discord.Forbidden:
                # Private archived threads need the Manage Threads permission.
                logger.info('No access to some archived threads in #%s', channel.name)

        for thread in threads.values():
            downloaded += await self._scan_messages(thread)
        return downloaded

    async def _scan_messages(self, channel: discord.abc.Messageable) -> int:
        """Go through messages from oldest to newest, saving the position as it goes."""
        location = _location(channel)
        downloaded = 0
        for attempt in range(1, SCAN_ATTEMPTS + 1):
            last_done = await self.db.get_progress(channel.id)
            last_message_id = getattr(channel, 'last_message_id', None)
            if last_done and last_message_id and last_message_id <= last_done:
                logger.debug('%s: nothing new', location)
                return downloaded

            after = discord.Object(id=last_done) if last_done is not None else None
            scanned = 0
            newest = None
            try:
                async for message in channel.history(limit=None, after=after, oldest_first=True):
                    downloaded += await self.archive_message(message)
                    newest = message.id
                    scanned += 1
                    if scanned % PROGRESS_EVERY == 0:
                        await self.db.set_progress(channel.id, newest)
                    if scanned % 1000 == 0:
                        logger.info('%s: %s messages scanned', location, scanned)
            except discord.Forbidden:
                logger.warning('%s: missing the Read Message History permission', location)
                return downloaded
            except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if newest is not None:
                    await self.db.set_progress(channel.id, newest)
                if attempt == SCAN_ATTEMPTS:
                    logger.error('%s: scan stopped, resuming on the next start: %s', location, exc)
                    return downloaded
                logger.warning('%s: scan interrupted (%s), resuming', location, exc)
                await asyncio.sleep(5 * attempt)
                continue

            if newest is not None:
                await self.db.set_progress(channel.id, newest)
            logger.info('%s: %s new message(s) scanned', location, scanned)
            return downloaded
        return downloaded

    async def _archive_attachment(
        self, attachment: discord.Attachment, message: discord.Message
    ) -> bool:
        """Record and download one attachment. Returns True if the file was downloaded now."""
        if attachment.id in self._in_flight:
            return False
        self._in_flight.add(attachment.id)
        try:
            info = AttachmentInfo(
                id=attachment.id,
                message_id=message.id,
                channel_id=message.channel.id,
                guild_id=message.guild.id if message.guild else None,
                author_id=message.author.id,
                filename=attachment.filename,
                content_type=attachment.content_type,
                size=attachment.size,
                url=_strip_signature(attachment.url),
                message_url=message.jump_url,
                message_date=message.created_at,
            )
            # The details are stored first, so the link is kept even if the download fails.
            if await self.db.record_attachment(info):
                return False

            path = self._folder_for(message.channel) / _file_name(attachment)
            try:
                await self._download(attachment.url, path)
            except DownloadGone as exc:
                logger.warning(
                    '%s (%s) is no longer available: %s',
                    attachment.filename, message.jump_url, exc,
                )
                await self.db.mark_unavailable(attachment.id)
                return False
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error(
                    'Could not download %s (%s), retrying on the next start: %s',
                    attachment.filename, message.jump_url, _describe(exc),
                )
                return False
            except OSError as exc:
                logger.error('Could not save %s to %s: %s', attachment.filename, path, exc)
                return False

            relative_path = path.relative_to(self.archive_dir).as_posix()
            await self.db.mark_downloaded(attachment.id, relative_path)
            logger.debug('Saved %s as %s', attachment.filename, path)
            return True
        finally:
            self._in_flight.discard(attachment.id)

    async def _download(self, url: str, path: Path) -> None:
        """Stream a file to disk. It only appears under its final name once complete."""
        assert self.session is not None, 'Archiver.start() was not called'
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + '.part')
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                async with self.session.get(url) as response:
                    if response.status in GONE_STATUSES:
                        raise DownloadGone(f'HTTP {response.status}')
                    response.raise_for_status()
                    with partial.open('wb') as file:
                        async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                            file.write(chunk)
                partial.replace(path)
                return
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                partial.unlink(missing_ok=True)
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise
                delay = _retry_after(exc) or 2 ** attempt
                logger.warning('Download failed (%s), retrying in %ss', _describe(exc), delay)
                await asyncio.sleep(delay)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise

    async def _get_channel(self, channel_id: int):
        # Archived threads are not in the cache.
        return self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)

    def _folder_for(self, channel) -> Path:
        """<channel>/ for channels, <channel>/<thread>/ for threads and forum posts."""
        if isinstance(channel, discord.Thread):
            parent_name = channel.parent.name if channel.parent else ''
            return (
                self.archive_dir
                / _safe_name(parent_name, channel.parent_id)
                / _safe_name(channel.name, channel.id)
            )
        return self.archive_dir / _safe_name(getattr(channel, 'name', ''), channel.id)


def _attachments(message: discord.Message) -> list[discord.Attachment]:
    """The message's own attachments plus those of forwarded messages."""
    found = list(message.attachments)
    for snapshot in message.message_snapshots:
        found.extend(snapshot.attachments)
    return found


def _strip_signature(url: str) -> str:
    """Drop the ex/is/hm query parameters, which expire. Discord can sign the bare URL again."""
    return url.split('?', 1)[0]


def _safe_name(name: str, fallback_id: int) -> str:
    """A file or folder name that is valid everywhere and cannot escape the archive folder."""
    cleaned = UNSAFE_NAME_CHARS.sub('_', name).strip().rstrip('. ')[:MAX_NAME_LENGTH]
    if cleaned.strip('.') == '':
        return str(fallback_id)
    return cleaned


def _file_name(attachment: discord.Attachment) -> str:
    """<attachment id>_<original name>, so names never clash and stay readable."""
    name = _safe_name(attachment.filename, attachment.id)
    return f'{attachment.id}_{name}'


def _location(channel) -> str:
    if isinstance(channel, discord.Thread):
        parent = channel.parent.name if channel.parent else channel.parent_id
        return f'#{parent} > {channel.name}'
    return f'#{getattr(channel, "name", channel.id)}'


def _describe(exc: BaseException) -> str:
    if isinstance(exc, aiohttp.ClientResponseError):
        return f'HTTP {exc.status}'
    return str(exc) or type(exc).__name__


def _retry_after(exc: BaseException) -> float | None:
    if not isinstance(exc, aiohttp.ClientResponseError) or exc.headers is None:
        return None
    try:
        return float(exc.headers.get('Retry-After', ''))
    except ValueError:
        return None
