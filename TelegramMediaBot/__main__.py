import asyncio
import logging

from TelegramMediaBot.plugins.config import config
from TelegramMediaBot.plugins.handler import TelegramBot

logger = logging.getLogger(__name__)


async def main():
    bot = TelegramBot(config.API_ID, config.API_HASH, config.TOKEN)
    try:
        await bot.init_telegram_client()
        await bot.client.run_until_disconnected()
    finally:
        if bot.user_client:
            await bot.user_client.disconnect()
        await bot.client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
