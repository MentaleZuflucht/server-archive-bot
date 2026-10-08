import discord
from discord.ext import commands
import logging
from config import get_bot_config
from logging_config import setup_logging
from database import DatabaseManager
from cogs.events import BotEvents
import asyncio
import atexit

# Setup logging with the specified configuration path
setup_logging()
bot_logger = logging.getLogger('bot')
bot_logger.info('Logging setup complete')

# Intents: guild cache, live guild messages, and attachment data on those messages
intents = discord.Intents(guilds=True, guild_messages=True, message_content=True)
bot_logger.debug(f'Intents setup complete: {intents}')

# Create bot instance
bot = commands.Bot(command_prefix='!', intents=intents)

# Load bot configuration
bot.config = get_bot_config()
bot_logger.info('Bot configuration loaded')

# Initialize database
bot.db_manager = DatabaseManager()
bot_logger.info('Database initialized')


def cleanup():
    """Clean up resources on exit."""
    if hasattr(bot, 'db_manager'):
        bot.db_manager.close()
        bot_logger.info('Database connections closed')


atexit.register(cleanup)


@bot.event
async def on_ready():
    """Log bot readiness."""
    bot_logger.info(f'Logged in as {bot.user}')
    bot_logger.info('Bot ready')


async def main():
    """
    Loads all cog extensions and starts the Discord bot.
    """
    try:
        await bot.add_cog(BotEvents(bot))
        await bot.start(bot.config.token)
    except Exception as e:
        bot_logger.error(f"Error starting bot: {e}")
        raise
    finally:
        cleanup()


if __name__ == "__main__":
    asyncio.run(main())
