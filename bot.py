"""Discord bot that archives every attachment posted in the configured channels."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import discord
from dotenv import load_dotenv

from archiver import Archiver
from config import ConfigError, Settings, load_settings
from database import Database
from logging_config import setup_logging

logger = logging.getLogger('bot')

# The bot never joins voice, so don't warn about missing voice dependencies.
discord.VoiceClient.warn_nacl = False
discord.VoiceClient.warn_dave = False


class ArchiveBot(discord.Client):
    def __init__(self, settings: Settings, db: Database) -> None:
        # Guild cache, live guild messages, and the attachments on them.
        # Attachments count as message content, so that privileged intent is required.
        intents = discord.Intents(guilds=True, guild_messages=True, message_content=True)
        super().__init__(
            intents=intents,
            # Messages are handled once and never looked up again, so don't cache them.
            max_messages=None,
            member_cache_flags=discord.MemberCacheFlags.none(),
        )
        self.settings = settings
        self.archiver = Archiver(self, db, settings.archive_dir, settings.channel_ids)
        self._startup_task: asyncio.Task | None = None

    async def setup_hook(self) -> None:
        await self.archiver.start()
        # setup_hook runs once, unlike on_ready, which fires again after reconnects.
        self._startup_task = asyncio.create_task(self._run_startup_jobs())

    async def _run_startup_jobs(self) -> None:
        await self.wait_until_ready()
        try:
            await self.archiver.retry_failed_downloads()
            if self.settings.archive_history:
                await self.archiver.archive_history()
        except Exception:
            logger.exception('Archiving the history failed')

    async def on_ready(self) -> None:
        logger.info('Logged in as %s', self.user)
        for channel_id in self.settings.channel_ids:
            if self.get_channel(channel_id) is None:
                logger.warning('Channel %s is not visible to the bot', channel_id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user or not self.archiver.is_monitored(message.channel):
            return
        downloaded = await self.archiver.archive_message(message)
        if downloaded:
            logger.info('Archived %s file(s) from %s', downloaded, message.jump_url)

    async def close(self) -> None:
        if self._startup_task is not None:
            self._startup_task.cancel()
        await self.archiver.close()
        await super().close()


async def run(settings: Settings) -> None:
    db = Database(settings.database_url)
    try:
        await db.init()
        async with ArchiveBot(settings, db) as bot:
            # docker stop sends SIGTERM. Shut down cleanly instead of being killed.
            # Windows has no signal handlers in asyncio; Ctrl+C still works there.
            with contextlib.suppress(NotImplementedError):
                asyncio.get_running_loop().add_signal_handler(
                    signal.SIGTERM, lambda: asyncio.create_task(bot.close())
                )
            await bot.start(settings.token)
    finally:
        await db.close()


def main() -> None:
    load_dotenv()
    setup_logging()
    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.critical('%s', exc)
        raise SystemExit(1) from None
    logger.info('Archiving %s channel(s) to %s', len(settings.channel_ids), settings.archive_dir)

    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except discord.LoginFailure:
        logger.critical('Discord rejected the token. Check DISCORD_TOKEN.')
        raise SystemExit(1) from None
    except discord.PrivilegedIntentsRequired:
        logger.critical(
            'Enable the Message Content intent for the bot in the Discord Developer Portal.'
        )
        raise SystemExit(1) from None
    except Exception:
        logger.critical('The bot stopped because of an unhandled error.', exc_info=True)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
