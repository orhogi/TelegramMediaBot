import logging

logger = logging.getLogger(__name__)


class AutoForward:
    def __init__(self, bot_client, forward_chat_id):
        self.bot = bot_client
        self.forward_chat = None
        self.chat_id = forward_chat_id

    async def setup(self):
        if not self.chat_id:
            return
        try:
            self.forward_chat = await self.bot.get_entity(self.chat_id)
            logger.info(f"Auto-forward enabled to: {self.chat_id}")
        except Exception as e:
            logger.error(f"Failed to resolve forward chat {self.chat_id}: {e}")
            self.forward_chat = None

    async def forward_media(self, source_chat_id, source_message_id):
        if not self.forward_chat:
            return
        try:
            await self.bot.forward_messages(
                self.chat_id, source_chat_id, source_message_id
            )
        except Exception as e:
            logger.warning(f"Auto-forward failed: {e}")

    async def send_copy(self, chat_id, message_id):
        if not self.forward_chat:
            return
        try:
            await self.bot.copy_message(self.chat_id, chat_id, message_id)
        except Exception as e:
            logger.warning(f"Auto-forward copy failed: {e}")

    @property
    def enabled(self):
        return self.forward_chat is not None
