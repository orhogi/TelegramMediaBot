import logging

logger = logging.getLogger(__name__)


class AutoForward:
    def __init__(self, bot_client, forward_chat_id):
        self.bot = bot_client
        self.forward_chat = None
        self.chat_id = self._coerce(forward_chat_id)

    @staticmethod
    def _coerce(value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

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
                self.forward_chat, source_message_id, source_chat_id
            )
        except Exception as e:
            logger.warning(f"Auto-forward failed: {e}")

    async def send_copy(self, source_chat_id, source_message_id):
        await self.forward_media(source_chat_id, source_message_id)

    @property
    def enabled(self):
        return self.forward_chat is not None
