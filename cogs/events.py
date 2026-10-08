import discord
from discord.ext import commands
import logging
import aiohttp
import asyncio
import secrets
from pathlib import Path
from datetime import datetime


class BotEvents(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot_logger = logging.getLogger('bot.events')
        self.processed_count = 0
        self.session = None

    async def _ensure_session(self):
        """Ensure aiohttp session is available."""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=60, connect=30)
            self.session = aiohttp.ClientSession(timeout=timeout)

    async def _close_session(self):
        """Close aiohttp session."""
        if self.session and not self.session.closed:
            await self.session.close()

    @commands.Cog.listener()
    async def on_ready(self):
        """Logs bot readiness and initiates archiving if enabled."""
        self.bot_logger.info(f'Logged in as {self.bot.user}')
        self.bot_logger.info('Bot ready')

        if self.bot.config.archive_history:
            self.bot_logger.debug(f'Archiving pictures in these channels: {self.bot.config.channel_ids}')
            try:
                await self._ensure_session()
                await self.archive_pictures()
            except Exception as e:
                self.bot_logger.error(f"Error during archiving: {e}")
            finally:
                await self._close_session()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handles incoming messages and downloads attachments if applicable."""
        if message.author == self.bot.user:
            self.bot_logger.debug('Ignoring message from self')
            return

        is_monitored = message.channel.id in self.bot.config.channel_ids

        # If not in monitored channel, check if it's a thread of a monitored channel
        if not is_monitored and hasattr(message.channel, 'parent'):
            is_monitored = message.channel.parent and message.channel.parent.id in self.bot.config.channel_ids
            self.bot_logger.debug(f'Message is in a thread of a monitored channel: {message.channel.id}')

        if is_monitored:
            channel_name = message.channel.name
            thread_name = message.channel.name if hasattr(message.channel, 'parent') else None
            self.bot_logger.debug(f'Message is in a monitored channel or thread: {message.channel.id}')

            for attachment in message.attachments:
                self.bot_logger.info(f'Found attachment: {attachment.filename}')
                try:
                    await self._ensure_session()
                    await self.download_attachment(
                        attachment, message.channel, channel_name, thread_name, message.created_at
                    )
                except Exception as e:
                    self.bot_logger.error(f"Error downloading attachment {attachment.filename}: {e}")
        else:
            self.bot_logger.debug(f'Message is not in a monitored channel or thread: {message.channel.id}')

    async def archive_pictures(self):
        """Archives pictures from specified channels and their threads with improved error handling."""
        self.processed_count = 0
        start_time = datetime.now()
        try:
            for channel_id in self.bot.config.channel_ids:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    self.bot_logger.warning(f'Channel with ID {channel_id} not found')
                    continue

                channel_name = channel.name
                self.bot_logger.info(f'Starting archival for channel: {channel_name} (ID: {channel_id})')

                # Process main channel messages
                await self._process_channel_messages(channel, channel_name, None)

                # Handle active threads
                self.bot_logger.info(f'Processing active threads in channel: {channel_name}')
                for thread in channel.threads:
                    await self._process_channel_messages(thread, channel_name, thread.name)

                # Handle archived threads (this is important for old threads!)
                self.bot_logger.info(f'Processing archived threads in channel: {channel_name}')
                try:
                    async for thread in channel.archived_threads(limit=None):
                        await self._process_channel_messages(thread, channel_name, thread.name)
                except discord.Forbidden:
                    self.bot_logger.warning(f'No permission to access archived threads in {channel_name}')
                except Exception as e:
                    self.bot_logger.error(f'Error accessing archived threads in {channel_name}: {e}')

            elapsed_time = datetime.now() - start_time
            self.bot_logger.info(
                f'Completed archival! Processed {self.processed_count} attachments in {elapsed_time}'
            )

        except Exception as e:
            self.bot_logger.error(f"Critical error during archiving: {e}")
            raise

    async def _process_channel_messages(self, channel, channel_name: str, thread_name: str = None):
        """Process messages from a channel or thread with rate limiting and error recovery."""
        location_name = f"{channel_name}/{thread_name}" if thread_name else channel_name
        self.bot_logger.info(f'Processing messages in: {location_name}')

        message_count = 0
        attachment_count = 0
        retry_count = 0
        max_retries = 3

        while retry_count <= max_retries:
            try:
                async for message in channel.history(limit=None):
                    message_count += 1

                    # Progress logging every 1000 messages
                    if message_count % 1000 == 0:
                        self.bot_logger.info(f'Processed {message_count} messages in {location_name}')

                    for attachment in message.attachments:
                        attachment_count += 1
                        self.processed_count += 1

                        try:
                            await self.download_attachment(
                                attachment, channel, channel_name, thread_name, message.created_at
                            )

                            # Rate limiting: small delay every 10 attachments
                            if self.processed_count % 10 == 0:
                                await asyncio.sleep(0.1)

                            # Progress logging every 100 attachments
                            if self.processed_count % 100 == 0:
                                self.bot_logger.info(f'Total attachments processed: {self.processed_count}')

                        except discord.HTTPException as e:
                            if e.status == 429:  # Rate limited
                                retry_after = getattr(e, 'retry_after', 5)
                                self.bot_logger.warning(f'Rate limited, waiting {retry_after} seconds')
                                await asyncio.sleep(retry_after)
                            else:
                                self.bot_logger.error(f'HTTP error downloading {attachment.filename}: {e}')
                        except Exception as e:
                            self.bot_logger.error(f'Error downloading {attachment.filename}: {e}')

                # If we get here, processing completed successfully
                self.bot_logger.info(
                    f'Completed {location_name}: {message_count} messages, {attachment_count} attachments'
                )
                break

            except discord.HTTPException as e:
                retry_count += 1
                if e.status == 429:  # Rate limited
                    retry_after = getattr(e, 'retry_after', 10)
                    self.bot_logger.warning(
                        f'Rate limited on {location_name}, waiting {retry_after} seconds '
                        f'(retry {retry_count}/{max_retries})'
                    )
                    await asyncio.sleep(retry_after)
                else:
                    self.bot_logger.error(f'HTTP error in {location_name}: {e}')
                    if retry_count > max_retries:
                        raise
                    await asyncio.sleep(5)
            except Exception as e:
                retry_count += 1
                self.bot_logger.error(
                    f'Error processing {location_name} (retry {retry_count}/{max_retries}): {e}'
                )
                if retry_count > max_retries:
                    raise
                await asyncio.sleep(5)

    async def download_attachment(
        self, attachment, channel, channel_name: str, thread_name: str = None, message_date=None
    ):
        """Downloads an attachment if it hasn't been downloaded already."""
        # Check if already downloaded using database
        if self.bot.db_manager.is_downloaded(attachment.url):
            self.bot_logger.debug(f'Attachment already downloaded: {attachment.url}')
            return

        # Create directory path based on channel and thread names
        directory_path = self.bot.config.archive_dir / channel_name
        if thread_name:
            directory_path = directory_path / thread_name

        # Ensure the directory exists
        directory_path.mkdir(parents=True, exist_ok=True)

        self.bot_logger.debug(f'Downloading attachment: {attachment.filename}')

        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self._ensure_session()

                async with self.session.get(attachment.url) as response:
                    if response.status == 429:  # Rate limited
                        retry_after = int(response.headers.get('Retry-After', 5))
                        self.bot_logger.warning(
                            f'Rate limited downloading {attachment.filename}, waiting {retry_after} seconds'
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    content = await response.read()

                # Generate random filename with original extension
                file_extension = Path(attachment.filename).suffix
                random_filename = f"{secrets.token_hex(10)}{file_extension}"
                file_path = directory_path / random_filename

                # Write file asynchronously
                with open(file_path, 'wb') as output:
                    output.write(content)

                # Add to database
                success = self.bot.db_manager.add_attachment(
                    url=attachment.url,
                    filename=attachment.filename,
                    channel_id=channel.id,
                    message_date=message_date
                )

                if success:
                    self.bot_logger.debug(f'Attachment saved as: {random_filename}')
                else:
                    self.bot_logger.debug(f'Attachment URL already in database: {attachment.url}')

                return  # Success, exit retry loop

            except aiohttp.ClientError as e:
                self.bot_logger.error(
                    f"HTTP error downloading {attachment.filename} (attempt {attempt + 1}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
            except OSError as e:
                self.bot_logger.error(f"Failed to save attachment {attachment.filename}: {e}")
                break  # Don't retry file system errors
            except Exception as e:
                self.bot_logger.error(
                    f"Unexpected error downloading {attachment.filename} (attempt {attempt + 1}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        self.bot_logger.error(f"Failed to download {attachment.filename} after {max_retries} attempts")

    def cog_unload(self):
        """Clean up when cog is unloaded."""
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())


def setup(bot):
    bot.add_cog(BotEvents(bot))
