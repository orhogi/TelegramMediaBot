import logging
import time

from .utils import format_bytes, format_time

logger = logging.getLogger(__name__)


class Progress:
    def __init__(self, client, chat_id, message_id, action="Downloading"):
        self.client = client
        self.chat_id = chat_id
        self.message_id = message_id
        self.action = action
        self.start_time = time.time()
        self.last_update = 0
        self.last_text = ""

    async def __call__(self, current, total):
        if total is None:
            total = 0

        now = time.time()
        if total and current < total and now - self.last_update < 4.0:
            return
        self.last_update = now

        if total == 0:
            elapsed = now - self.start_time
            text = (
                f"<b>{self.action}...</b>\n"
                f"<code>{format_bytes(current)}</code> | "
                f"<code>{format_bytes(current / max(elapsed, 0.1))}/s</code>"
            )
        else:
            elapsed = now - self.start_time
            speed = current / max(elapsed, 0.1)
            percent = min(current / total * 100, 100)
            eta = max((total - current) / speed, 0) if speed > 0 else 0

            bar_length = 12
            filled = int(bar_length * current / total)
            bar = "\u2588" * filled + "\u2591" * (bar_length - filled)

            text = (
                f"<b>{self.action}</b> <code>{percent:.1f}%</code>\n"
                f"<code>[{bar}]</code>\n"
                f"<code>{format_bytes(current)}</code> / "
                f"<code>{format_bytes(total)}</code>\n"
                f"Speed: <code>{format_bytes(speed)}/s</code> | "
                f"ETA: <code>{format_time(eta)}</code>"
            )

        if text != self.last_text:
            self.last_text = text
            try:
                await self.client.edit_message(
                    self.chat_id, self.message_id, text, parse_mode="html"
                )
            except Exception:
                pass

    async def finish(self):
        try:
            await self.client.edit_message(
                self.chat_id, self.message_id, f"<b>{self.action} complete</b>",
                parse_mode="html",
            )
        except Exception:
            pass
