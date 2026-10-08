import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
from config import ConfigError, load_settings
from logging_config import setup_logging
from database import Database
from cogs.events import BotEvents
import asyncio

bot_logger = logging.getLogger('bot')


async def main():
    """
    Loads settings, adds the cog and starts the Discord bot.
    """
    load_dotenv()
    setup_logging()

    try:
        settings = load_settings()
    except ConfigError as e:
        bot_logger.critical(e)
        raise SystemExit(1) from None

    # Intents: guild cache, live guild messages, and attachment data on those messages
    intents = discord.Intents(guilds=True, guild_messages=True, message_content=True)
    bot = commands.Bot(command_prefix='!', intents=intents)
    bot.config = settings
    bot.db = Database(settings.database_url)

    try:
        await bot.db.init()
        async with bot:
            await bot.add_cog(BotEvents(bot))
            await bot.start(settings.token)
    finally:
        await bot.db.close()


if __name__ == "__main__":
    asyncio.run(main())
