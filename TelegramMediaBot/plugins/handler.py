import asyncio
import html
import json
import logging
import os
from collections import OrderedDict
from datetime import datetime

from telethon import Button, TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.stories import (
    GetPeerStoriesRequest,
    GetStoriesByIDRequest,
)
from telethon.tl.types import DocumentAttributeVideo, StoryItem
from telethon.errors import FloodWaitError

from .config import config
from .forward import AutoForward
from .progress import Progress
from .utils import (
    clean_all_downloads,
    cleanup_download,
    format_bytes,
    get_audio_tags,
    get_download_path,
    get_file_name,
    get_media_dimensions,
    get_media_duration,
    get_media_info,
    get_thumb_path,
    generate_video_thumbnail,
    is_within_upload_limit,
    parse_story_url,
    parse_tg_url,
    prepare_caption,
    size_of_downloads,
)

MAX_PROCESSED_GROUPS = 1000
SESSION_REVEAL_TTL_SECONDS = 300
DOWNLOADED_STORIES_FILE = "sessions/downloaded_stories.json"


def _story_state_key(username):
    return username.lstrip("@").lower()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, api_id, api_hash, bot_token):
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        os.makedirs("sessions", exist_ok=True)
        self.client = TelegramClient("sessions/tgstorydl", self.api_id, self.api_hash)
        self.user_client = self._create_user_client()
        self.forwarder = AutoForward(self.client, config.FORWARD_CHAT_ID)
        self.start_time = datetime.now()
        self.user_tasks = {}
        self.task_lock = asyncio.Lock()
        self.user_ids = set()
        self.downloaded_count = 0
        self.stats_lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)
        self.running_tasks = set()
        self.processed_media_groups = OrderedDict()
        self.user_ok = False
        self.login_sessions = {}
        self._last_summary_update = 0
        self.cleanup_in_progress = False
        self.story_state_lock = asyncio.Lock()
        self.downloaded_stories = self._load_downloaded_stories()

    @property
    def _getter(self):
        return self.user_client if self.user_ok else self.client

    def _create_user_client(self):
        if config.STRING_SESSION:
            try:
                return TelegramClient(
                    StringSession(config.STRING_SESSION), self.api_id, self.api_hash
                )
            except ValueError:
                logger.warning("Invalid STRING_SESSION")
                return None
        if os.path.exists("sessions/userbot.session"):
            return TelegramClient("sessions/userbot", self.api_id, self.api_hash)
        logger.warning("No STRING_SESSION or session file - use /login to create one")
        return None

    async def init_telegram_client(self):
        await self.client.start(bot_token=self.bot_token)

        if self.user_client:
            try:
                await self.user_client.start()
                me = await self.user_client.get_me()
                if me and not getattr(me, "bot", True):
                    self.user_ok = True
                    logger.info("User client authenticated successfully")
                else:
                    logger.warning(
                        "User client is a bot or unauthenticated - "
                        "stories and private posts will not work."
                    )
            except Exception as e:
                logger.error(f"User client failed to start: {e}")
                self.user_client = None
        else:
            logger.warning(
                "No user client available - use /login to generate a session"
            )

        await self.forwarder.setup()
        self.client.add_event_handler(self.handle_message, events.NewMessage)
        logger.info("Telegram client initialized.")

    async def handle_message(self, event):
        if not event.is_private:
            if not event.message.message.startswith("/"):
                return
            await event.reply(
                "<b>This bot works in private chat only.</b>\nPlease DM me to use commands.",
                parse_mode="html",
            )
            return

        message_text = event.message.message.strip()
        chat_id = event.chat_id

        async with self.stats_lock:
            self.user_ids.add(event.sender_id)

        if not message_text:
            return

        if event.sender_id in self.login_sessions:
            await self._handle_login_input(event)
            return

        if message_text.startswith("/"):
            await self._handle_command(event)
            return

        story_user, story_id = parse_story_url(message_text)
        if story_user and story_id:
            await self.download_archived_story(chat_id, story_user, story_id)
            return

        parsed = parse_tg_url(message_text)
        entity, msg_id, _ = parsed

        if msg_id:
            await self._handle_post_download(chat_id, message_text)
            return

        if entity:
            await self.download_story(chat_id, entity)
            return

        if message_text.startswith("@"):
            await self.download_story(chat_id, message_text.strip("@"))
            return

        await event.reply(
            "Send a username (<code>@user</code>), profile link, or post link.\n"
            "Use <code>/help</code> for all commands.",
            parse_mode="html",
        )

    async def _handle_command(self, event):
        message_text = event.message.message.strip()
        chat_id = event.chat_id
        sender_id = event.sender_id
        parts = message_text.split()
        cmd = parts[0].lower()

        if cmd == "/start":
            lines = [
                "<b>Telegram Story & Media Downloader</b>\n",
                "Send me:",
                "&#8226; <code>@username</code> to download stories",
                "&#8226; A post link to download media",
                "&#8226; <code>/dl link</code> to force post download",
                "&#8226; <code>/bdl start end</code> for batch download",
            ]
            if not self.user_ok:
                lines.append(
                    "\n<b>\u26a0 Session required!</b>\n"
                    "Use <code>/login</code> to generate a user session.\n"
                    "This enables story downloads and private post access."
                )
            lines.append("\nUse <code>/help</code> for full command list.")
            await event.reply(
                "".join(lines),
                parse_mode="html",
                buttons=[Button.url("Report Bugs", url="https://t.me/c_0_t_e")],
            )

        elif cmd == "/help":
            await self._handle_help(event)

        elif cmd == "/status":
            if sender_id not in config.DEVS:
                await event.reply("<b>Permission denied.</b>", parse_mode="html")
                return
            await self._show_status(event)

        elif cmd == "/stats":
            if sender_id not in config.DEVS:
                await event.reply("<b>Permission denied.</b>", parse_mode="html")
                return
            await self._show_stats(event)

        elif cmd == "/killall":
            if sender_id not in config.DEVS:
                await event.reply("<b>Permission denied.</b>", parse_mode="html")
                return
            await self._handle_killall(event)

        elif cmd == "/cleanup":
            if sender_id not in config.DEVS:
                await event.reply("<b>Permission denied.</b>", parse_mode="html")
                return
            await self._handle_cleanup(event)

        elif cmd == "/login":
            await self._handle_login_start(event)

        elif cmd == "/cancel":
            await self._cancel_login(event.sender_id, chat_id)

        elif cmd == "/dl":
            if len(parts) >= 2:
                await self._handle_post_download(chat_id, parts[1])
            else:
                await event.reply("<b>Usage:</b> <code>/dl &lt;t.me link&gt;</code>", parse_mode="html")

        elif cmd == "/bdl":
            if len(parts) >= 3:
                await self._handle_batch_download(chat_id, parts[1], parts[2])
            else:
                await event.reply(
                    "<b>Usage:</b> <code>/bdl &lt;start_url&gt; &lt;end_url&gt;</code>",
                    parse_mode="html",
                )

        elif cmd == "/sdl":
            if len(parts) >= 2:
                suser, sid = parse_story_url(parts[1])
                if suser and sid:
                    await self.download_archived_story(chat_id, suser, sid)
                else:
                    await event.reply("<b>Invalid story URL.</b>", parse_mode="html")
            else:
                await event.reply(
                    "<b>Usage:</b> <code>/sdl &lt;story_url&gt;</code>",
                    parse_mode="html",
                )

        else:
            await event.reply(
                f"<b>Unknown command:</b> <code>{html.escape(cmd)}</code>\n"
                "Use <code>/help</code> for the command list.",
                parse_mode="html",
            )

    async def _handle_help(self, event):
        await event.reply(
            "<b>Commands</b>\n\n"
            "<code>@username</code> \u2014 Download new stories only (skips already downloaded)\n"
            "<code>t.me/username</code> \u2014 Same as above\n"
            "<code>t.me/username/s/ID</code> \u2014 Download a single archived story\n"
            "<code>t.me/channel/msg_id</code> \u2014 Download media from a post\n"
            "<code>/dl &lt;url&gt;</code> \u2014 Force post download\n"
            "<code>/bdl &lt;url1&gt; &lt;url2&gt;</code> \u2014 Batch download post range\n"
            "<code>/sdl &lt;story_url&gt;</code> \u2014 Download a single archived story\n"
            "<code>/login</code> \u2014 Generate a user session string\n"
            "<code>/cancel</code> \u2014 Cancel an in-progress login\n"
            "<code>/start</code> \u2014 Welcome message\n"
            "<code>/help</code> \u2014 This help\n\n"
            "<b>Devs only</b>\n"
            "<code>/status</code> \u2014 Bot statistics\n"
            "<code>/stats</code> \u2014 System resources\n"
            "<code>/killall</code> \u2014 Cancel all running tasks\n"
            "<code>/cleanup</code> \u2014 Delete all temp download files",
            parse_mode="html",
        )

    async def _handle_killall(self, event):
        cancelled = 0
        tasks_to_await = []
        async with self.task_lock:
            for task in list(self.running_tasks):
                if not task.done():
                    task.cancel()
                    cancelled += 1
                    tasks_to_await.append(task)
            self.running_tasks.clear()
            self.user_tasks.clear()
        if tasks_to_await:
            await asyncio.gather(*tasks_to_await, return_exceptions=True)
        if cancelled == 0:
            await event.reply("<b>No running tasks.</b>", parse_mode="html")
        else:
            await event.reply(
                f"<b>Cancelled {cancelled} running task(s).</b>", parse_mode="html"
            )

    async def _handle_cleanup(self, event):
        async with self.task_lock:
            if any(not t.done() for t in self.running_tasks):
                await event.reply(
                    "<b>Cannot cleanup while tasks are running.</b> Use <code>/killall</code> first.",
                    parse_mode="html",
                )
                return
            if self.cleanup_in_progress:
                await event.reply("<b>Cleanup already in progress.</b>", parse_mode="html")
                return
            self.cleanup_in_progress = True

        try:
            removed, freed = await asyncio.to_thread(clean_all_downloads)
        finally:
            async with self.task_lock:
                self.cleanup_in_progress = False

        await event.reply(
            f"<b>Cleanup complete</b>\n"
            f"Files removed: <code>{removed}</code>\n"
            f"Space freed: <code>{format_bytes(freed)}</code>",
            parse_mode="html",
        )

    async def _show_status(self, event):
        async with self.stats_lock:
            total_users = len(self.user_ids)
            downloaded = self.downloaded_count
        uptime = datetime.now() - self.start_time

        d_count, d_size = size_of_downloads()
        async with self.task_lock:
            active = sum(1 for t in self.running_tasks if not t.done())

        await event.reply(
            f"<b>Bot Status</b>\n\n"
            f"Users: <code>{total_users}</code>\n"
            f"Downloads: <code>{downloaded}</code>\n"
            f"Active tasks: <code>{active}</code>\n"
            f"Temp files: <code>{d_count}</code> (<code>{format_bytes(d_size)}</code>)\n"
            f"Uptime: <code>{uptime}</code>",
            parse_mode="html",
        )

    async def _show_stats(self, event):
        import psutil

        uptime = datetime.now() - self.start_time
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(".")
        net = psutil.net_io_counters()

        await event.reply(
            f"<b>System Stats</b>\n\n"
            f"CPU: <code>{cpu}%</code>\n"
            f"RAM: <code>{format_bytes(mem.used)}</code> / "
            f"<code>{format_bytes(mem.total)}</code> "
            f"(<code>{mem.percent}%</code>)\n"
            f"Disk: <code>{format_bytes(disk.used)}</code> / "
            f"<code>{format_bytes(disk.total)}</code> "
            f"(<code>{disk.percent}%</code>)\n"
            f"Net sent: <code>{format_bytes(net.bytes_sent)}</code>\n"
            f"Net recv: <code>{format_bytes(net.bytes_recv)}</code>\n"
            f"Bot uptime: <code>{uptime}</code>",
            parse_mode="html",
        )

    # ------------------------------------------------------------------
    # In-Chat Session Generator
    # ------------------------------------------------------------------

    async def _handle_login_start(self, event):
        chat_id = event.chat_id
        user_id = event.sender_id

        if user_id in self.login_sessions:
            await self._cancel_login(user_id, chat_id)

        await event.reply(
            "<b>Session Generator</b>\n\n"
            "Send your phone number in international format.\n"
            "Example: <code>+1234567890</code>\n\n"
            "Use <code>/cancel</code> to abort.",
            parse_mode="html",
        )

        try:
            client = TelegramClient(StringSession(), self.api_id, self.api_hash)
            await client.connect()
        except Exception as e:
            await event.reply(f"<b>Failed to start login:</b> {e}", parse_mode="html")
            return

        self.login_sessions[user_id] = {
            "step": "phone",
            "client": client,
            "phone": None,
            "hash": None,
        }

    async def _handle_login_input(self, event):
        chat_id = event.chat_id
        user_id = event.sender_id
        message_text = event.message.message.strip()

        if message_text.lower() == "/cancel":
            await self._cancel_login(user_id, chat_id)
            return

        session = self.login_sessions.get(user_id)

        if not session:
            return

        if session["step"] == "phone":
            await self._login_phone(chat_id, user_id, message_text, session)
        elif session["step"] == "code":
            await self._login_code(chat_id, user_id, message_text, session)
        elif session["step"] == "password":
            await self._login_password(chat_id, user_id, message_text, session)

    async def _login_phone(self, chat_id, user_id, phone, session):
        if not phone.startswith("+") or not phone[1:].isdigit():
            await self.client.send_message(
                chat_id,
                "<b>Invalid format.</b> Use international format: <code>+1234567890</code>",
                parse_mode="html",
            )
            return

        try:
            sent = await session["client"].send_code_request(phone)
            session["phone"] = phone
            session["hash"] = sent.phone_code_hash
            session["step"] = "code"
            await self.client.send_message(
                chat_id,
                "<b>Code sent.</b> Send it with spaces (e.g. <code>1 2 3 4 5</code>)\n"
                "This prevents Telegram from deleting it — we strip spaces automatically.",
                parse_mode="html",
            )
        except Exception as e:
            logger.error(f"Login phone error: {e}")
            await self.client.send_message(
                chat_id,
                f"<b>Error:</b> {e}\nTry again or use <code>/cancel</code>.",
                parse_mode="html",
            )

    async def _login_code(self, chat_id, user_id, code, session):
        code = code.replace(" ", "").replace("-", "")
        try:
            await session["client"].sign_in(
                session["phone"], code, phone_code_hash=session["hash"]
            )
            await self._finish_login(chat_id, user_id, session)
        except Exception as e:
            error_str = str(e)
            if "PASSWORD_HASH_INVALID" in error_str or "2FA" in error_str.upper():
                session["step"] = "password"
                await self.client.send_message(
                    chat_id,
                    "<b>2FA is enabled.</b> Enter your password.",
                    parse_mode="html",
                )
            elif "PHONE_CODE_INVALID" in error_str or "PHONE_CODE_EXPIRED" in error_str:
                await self.client.send_message(
                    chat_id,
                    "<b>Wrong or expired code.</b> Try again or use <code>/cancel</code>.",
                    parse_mode="html",
                )
            else:
                logger.error(f"Login code error: {e}")
                await self.client.send_message(
                    chat_id,
                    f"<b>Error:</b> {e}\nTry again or use <code>/cancel</code>.",
                    parse_mode="html",
                )

    async def _login_password(self, chat_id, user_id, password, session):
        try:
            await session["client"].sign_in(password=password)
            await self._finish_login(chat_id, user_id, session)
        except Exception as e:
            logger.error(f"Login password error: {e}")
            await self.client.send_message(
                chat_id,
                "<b>Wrong password.</b> Try again or use <code>/cancel</code>.",
                parse_mode="html",
            )

    async def _finish_login(self, chat_id, user_id, session):
        try:
            session_string = session["client"].session.save()
        finally:
            try:
                await session["client"].disconnect()
            except Exception:
                pass
            self.login_sessions.pop(user_id, None)

        sent = await self.client.send_message(
            chat_id,
            "<b>\u26a0 Session generated</b>\n\n"
            f"<code>{session_string}</code>\n\n"
            "<b>Keep this secret!</b> Anyone with this string can access your Telegram account.\n"
            "Copy it to <code>STRING_SESSION</code> in your <code>.env</code> file "
            "and restart the bot.\n\n"
            f"<i>This message will self-destruct in {SESSION_REVEAL_TTL_SECONDS // 60} minutes.</i>",
            parse_mode="html",
        )
        asyncio.create_task(self._delete_after(sent, SESSION_REVEAL_TTL_SECONDS))

    @staticmethod
    async def _delete_after(message, delay):
        try:
            await asyncio.sleep(delay)
            await message.delete()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Story dedupe state
    # ------------------------------------------------------------------

    @staticmethod
    def _load_downloaded_stories():
        if not os.path.exists(DOWNLOADED_STORIES_FILE):
            return {}
        try:
            with open(DOWNLOADED_STORIES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return {k: set(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load story state: {e}")
            return {}

    @staticmethod
    def _write_stories_file_sync(data):
        os.makedirs(os.path.dirname(DOWNLOADED_STORIES_FILE), exist_ok=True)
        tmp = DOWNLOADED_STORIES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, DOWNLOADED_STORIES_FILE)

    async def _is_story_downloaded(self, key, story_id):
        async with self.story_state_lock:
            return story_id in self.downloaded_stories.get(key, set())

    async def _mark_story_downloaded(self, key, story_id):
        async with self.story_state_lock:
            self.downloaded_stories.setdefault(key, set()).add(story_id)
            data = {k: sorted(list(v)) for k, v in self.downloaded_stories.items()}
        try:
            await asyncio.to_thread(self._write_stories_file_sync, data)
        except OSError as e:
            logger.error(f"Failed to persist story state: {e}")

    async def _cancel_login(self, user_id, chat_id):
        session = self.login_sessions.pop(user_id, None)
        if session:
            try:
                await session["client"].disconnect()
            except Exception:
                pass
            await self.client.send_message(
                chat_id,
                "<b>Login cancelled.</b>",
                parse_mode="html",
            )

    # ------------------------------------------------------------------
    # Story Download
    # ------------------------------------------------------------------

    async def download_story(self, chat_id, username):
        if not self.user_ok:
            logger.info(f"Story download rejected for {username} — no user session")
            await self.client.send_message(
                chat_id,
                "<b>Story downloads require a valid user session.</b>\n"
                "Use <code>/login</code> to generate one.",
                parse_mode="html",
            )
            return
        try:
            msg = await self.client.send_message(
                chat_id,
                f"<b>Fetching stories from</b> <code>{username}</code>...",
                parse_mode="html",
            )
            async with self.task_lock:
                if self.cleanup_in_progress:
                    await msg.edit(
                        "<b>Cleanup in progress, please retry shortly.</b>",
                        parse_mode="html",
                    )
                    return
                if chat_id not in self.user_tasks:
                    self.user_tasks[chat_id] = []
                task = asyncio.create_task(self._story_task(chat_id, username, msg.id))
                self.user_tasks[chat_id].append(task)
                self.running_tasks.add(task)
        except Exception as e:
            logger.error(f"Error starting story download for {username}: {e}")
            await self.client.send_message(chat_id, f"Failed to start download for {username}")

    async def _story_task(self, chat_id, username, status_msg_id):
        try:
            async with self.semaphore:
                key = _story_state_key(username)
                stories = await self.user_client(GetPeerStoriesRequest(username))
                peer_stories = getattr(stories, "stories", None)
                if not peer_stories:
                    await self.client.edit_message(
                        chat_id, status_msg_id,
                        f"<b>No stories found for</b> <code>{username}</code>",
                        parse_mode="html",
                    )
                    return

                story_list = getattr(peer_stories, "stories", [])
                if not story_list:
                    await self.client.edit_message(
                        chat_id, status_msg_id,
                        f"<b>No stories found for</b> <code>{username}</code>",
                        parse_mode="html",
                    )
                    return

                new_stories = []
                already_count = 0
                for s in story_list:
                    if not await self._is_story_downloaded(key, s.id):
                        new_stories.append(s)
                    else:
                        already_count += 1

                if not new_stories:
                    await self.client.edit_message(
                        chat_id, status_msg_id,
                        f"<b>No new stories from @{username}</b> "
                        f"(all {already_count} already downloaded)",
                        parse_mode="html",
                    )
                    return

                await self.client.delete_messages(chat_id, status_msg_id)

                total = len(new_stories)
                for i, story in enumerate(new_stories):
                    idx = i + 1
                    progress_msg = await self.client.send_message(
                        chat_id,
                        f"<b>Story {idx}/{total} from</b> <code>{username}</code>",
                        parse_mode="html",
                    )
                    success = await self._download_single_story(
                        chat_id, username, story, progress_msg.id, idx, total
                    )
                    if success:
                        await self._mark_story_downloaded(key, story.id)
                    await self.client.edit_message(
                        chat_id, progress_msg.id,
                        f"<b>Story {idx}/{total}</b> \u2713 Complete",
                        parse_mode="html",
                    )

                await self.client.send_message(
                    chat_id,
                    f"All {total} new stories from <code>{username}</code> uploaded.",
                    parse_mode="html",
                )
        except Exception as e:
            logger.error(f"Story task error for {username}: {e}")
            await self.client.send_message(chat_id, f"Failed to download stories from {username}")
        finally:
            async with self.task_lock:
                self.running_tasks.discard(asyncio.current_task())
                if chat_id in self.user_tasks:
                    self.user_tasks[chat_id] = [t for t in self.user_tasks[chat_id] if not t.done()]
                    if not self.user_tasks[chat_id]:
                        del self.user_tasks[chat_id]

    async def _download_single_story(self, chat_id, username, story, progress_msg_id, idx, total):
        story_file = None
        story_thumb = None
        sent = None
        try:
            progress = Progress(self.client, chat_id, progress_msg_id,
                                f"Downloading story {idx}/{total}")
            story_file = await self.user_client.download_media(
                story.media, progress_callback=progress
            )
            try:
                story_thumb = await self.user_client.download_media(story.media, thumb=-1)
            except Exception as e:
                logger.warning(f"Failed to download story thumb for {username}: {e}")
                story_thumb = None

            attributes = (
                story.media.document.attributes
                if hasattr(story.media, "document")
                else None
            )
            upload_progress = Progress(self.client, chat_id, progress_msg_id,
                                       f"Uploading story {idx}/{total}")
            sent = await self.client.send_file(
                chat_id,
                story_file,
                attributes=attributes,
                thumb=story_thumb,
                caption=story.caption,
                progress_callback=upload_progress,
            )
            async with self.stats_lock:
                self.downloaded_count += 1
            if sent:
                await self.forwarder.forward_media(chat_id, sent.id)
        except Exception as e:
            logger.error(f"Error downloading story from {username}: {e}")
        finally:
            if story_file and os.path.exists(story_file):
                try:
                    os.remove(story_file)
                except OSError:
                    pass
            if story_thumb and os.path.exists(story_thumb):
                try:
                    os.remove(story_thumb)
                except OSError:
                    pass
        return sent is not None

    # ------------------------------------------------------------------
    # Archived Story Download
    # ------------------------------------------------------------------

    async def download_archived_story(self, chat_id, username, story_id):
        if not self.user_ok:
            logger.info(f"Archived story download rejected for {username}/{story_id} — no user session")
            await self.client.send_message(
                chat_id,
                "<b>Story downloads require a valid user session.</b>\n"
                "Use <code>/login</code> to generate one.",
                parse_mode="html",
            )
            return
        try:
            msg = await self.client.send_message(
                chat_id,
                f"<b>Fetching archived story</b> <code>{username}/s/{story_id}</code>...",
                parse_mode="html",
            )
            async with self.task_lock:
                if self.cleanup_in_progress:
                    await msg.edit(
                        "<b>Cleanup in progress, please retry shortly.</b>",
                        parse_mode="html",
                    )
                    return
                if chat_id not in self.user_tasks:
                    self.user_tasks[chat_id] = []
                task = asyncio.create_task(
                    self._archived_story_task(chat_id, username, story_id, msg.id)
                )
                self.user_tasks[chat_id].append(task)
                self.running_tasks.add(task)
        except Exception as e:
            logger.error(f"Error starting archived story download for {username}/{story_id}: {e}")
            await self.client.send_message(
                chat_id, f"Failed to start download for {username}/s/{story_id}"
            )

    async def _archived_story_task(self, chat_id, username, story_id, status_msg_id):
        try:
            async with self.semaphore:
                key = _story_state_key(username)

                if await self._is_story_downloaded(key, story_id):
                    await self.client.edit_message(
                        chat_id, status_msg_id,
                        f"<b>Story</b> <code>{username}/s/{story_id}</code> \u2014 already downloaded",
                        parse_mode="html",
                    )
                    return

                try:
                    result = await self.user_client(
                        GetStoriesByIDRequest(peer=username, id=[story_id])
                    )
                except FloodWaitError as fw:
                    wait = fw.seconds + 1
                    await self.client.edit_message(
                        chat_id, status_msg_id,
                        f"<b>Flood wait</b> \u2014 Telegram requires a {fw.seconds}s pause.\n"
                        f"Waiting <code>{wait}s</code> before retrying...",
                        parse_mode="html",
                    )
                    logger.warning(f"Flood wait {fw.seconds}s on archived story {username}/s/{story_id}")
                    await asyncio.sleep(wait)
                    result = await self.user_client(
                        GetStoriesByIDRequest(peer=username, id=[story_id])
                    )
                stories_list = getattr(result, "stories", [])
                story = next((s for s in stories_list if isinstance(s, StoryItem)), None)

                if not story:
                    await self.client.edit_message(
                        chat_id, status_msg_id,
                        f"<b>Story</b> <code>{username}/s/{story_id}</code> not found or inaccessible.",
                        parse_mode="html",
                    )
                    return

                await self.client.edit_message(
                    chat_id, status_msg_id,
                    f"<b>Story</b> <code>{username}/s/{story_id}</code> \u2014 downloading...",
                    parse_mode="html",
                )

                success = await self._download_single_story(
                    chat_id, username, story, status_msg_id, 1, 1
                )
                if success:
                    await self._mark_story_downloaded(key, story_id)

                await self.client.edit_message(
                    chat_id, status_msg_id,
                    f"<b>Story</b> <code>{username}/s/{story_id}</code> \u2713 Complete",
                    parse_mode="html",
                )
        except Exception as e:
            logger.error(f"Archived story task error for {username}/s/{story_id}: {e}")
            await self.client.send_message(
                chat_id, f"Failed to download archived story {username}/s/{story_id}"
            )
        finally:
            async with self.task_lock:
                self.running_tasks.discard(asyncio.current_task())
                if chat_id in self.user_tasks:
                    self.user_tasks[chat_id] = [
                        t for t in self.user_tasks[chat_id] if not t.done()
                    ]
                    if not self.user_tasks[chat_id]:
                        del self.user_tasks[chat_id]

    # ------------------------------------------------------------------
    # Post Download
    # ------------------------------------------------------------------

    async def _handle_post_download(self, chat_id, url):
        entity, msg_id, thread_id = parse_tg_url(url)
        if not entity or not msg_id:
            await self.client.send_message(chat_id, "Invalid post link.")
            return

        async with self.task_lock:
            if self.cleanup_in_progress:
                await self.client.send_message(
                    chat_id, "<b>Cleanup in progress, please retry shortly.</b>",
                    parse_mode="html",
                )
                return
            task = asyncio.create_task(
                self._post_download_task(chat_id, entity, msg_id, thread_id)
            )
            self.running_tasks.add(task)

    async def _claim_media_group(self, group_key):
        async with self.task_lock:
            if group_key in self.processed_media_groups:
                self.processed_media_groups.move_to_end(group_key)
                return False
            self.processed_media_groups[group_key] = True
            while len(self.processed_media_groups) > MAX_PROCESSED_GROUPS:
                self.processed_media_groups.popitem(last=False)
            return True

    async def _post_download_task(self, chat_id, entity, msg_id, thread_id=None):
        try:
            async with self.semaphore:
                status_msg = await self.client.send_message(
                    chat_id,
                    f"<b>Fetching post</b> <code>{entity}/{msg_id}</code>...",
                    parse_mode="html",
                )

                try:
                    kwargs = dict(entity=entity, ids=msg_id)
                    if thread_id:
                        kwargs["reply_to"] = thread_id
                    message = await self._getter.get_messages(**kwargs)
                except Exception as e:
                    logger.error(f"Failed to get message {entity}/{msg_id}: {e}")
                    hint = ""
                    if not self.user_ok:
                        hint = (
                            "\nPrivate posts require a user session. "
                            "Set STRING_SESSION in .env (from @genStr_robot)."
                        )
                    await status_msg.edit(
                        f"<b>Failed to fetch post.</b> May not be accessible.{hint}",
                        parse_mode="html",
                    )
                    return

                if not message:
                    await status_msg.edit(
                        "<b>Post not found or not accessible.</b>", parse_mode="html"
                    )
                    return

                success = await self._process_post_message(
                    chat_id, message, status_msg, entity
                )

                if success:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Post download task error: {e}")
            try:
                await self.client.send_message(
                    chat_id, f"<b>Error:</b> {html.escape(str(e))}", parse_mode="html"
                )
            except Exception:
                pass
        finally:
            async with self.task_lock:
                self.running_tasks.discard(asyncio.current_task())

    async def _process_post_message(self, chat_id, message, status_msg, entity):
        if message.poll:
            await status_msg.edit("<b>Polls cannot be downloaded.</b>", parse_mode="html")
            return False

        if message.grouped_id:
            group_key = (entity, message.grouped_id)
            if await self._claim_media_group(group_key):
                group_messages = await self._get_media_group(
                    entity, message.id, message.grouped_id
                )
                if len(group_messages) > 1:
                    return await self._process_media_group(
                        chat_id, group_messages, status_msg
                    )

        if message.media:
            return await self._download_and_send_media(
                chat_id, message, status_msg, entity
            )
        elif message.text:
            sent_msg = await self.client.send_message(
                chat_id,
                message.message or "",
                formatting_entities=list(message.entities) if message.entities else None,
            )
            await self.forwarder.send_copy(chat_id, sent_msg.id)
            async with self.stats_lock:
                self.downloaded_count += 1
            await status_msg.edit("<b>Text sent.</b>", parse_mode="html")
            return True
        else:
            await status_msg.edit(
                "<b>No downloadable content in this post.</b>", parse_mode="html"
            )
            return False

    async def _get_media_group(self, entity, msg_id, grouped_id):
        try:
            start = max(1, msg_id - 10)
            end = msg_id + 10
            messages = await self._getter.get_messages(
                entity, ids=list(range(start, end + 1))
            )
            group = [m for m in messages if m and m.grouped_id == grouped_id]
            group.sort(key=lambda m: m.id)
            return group
        except Exception as e:
            logger.warning(f"Failed to fetch media group for {entity}/{msg_id}: {e}")
            return []

    async def _process_media_group(self, chat_id, messages, status_msg):
        downloaded = []
        try:
            for msg in messages:
                if not msg.media:
                    continue
                if msg.file and not is_within_upload_limit(msg.file.size):
                    await status_msg.edit(
                        f"<b>Skipping {msg.id}: exceeds upload limit.</b>",
                        parse_mode="html",
                    )
                    continue
                progress = Progress(self.client, chat_id, status_msg.id, "Downloading")
                path = get_download_path(chat_id, msg.id, get_file_name(msg))
                dl_path = await self._getter.download_media(
                    msg, file=path, progress_callback=progress
                )
                if dl_path:
                    downloaded.append((msg, dl_path))

            if not downloaded:
                await status_msg.edit(
                    "<b>Failed to download media group.</b>", parse_mode="html"
                )
                return False

            has_video = any(
                m.video or (
                    m.document and getattr(m.document, "mime_type", "").startswith("video/")
                )
                for m, _ in downloaded
            )

            if not has_video:
                album_files = [path for _, path in downloaded]
                caption, entities = prepare_caption(downloaded[0][0])
                sent = await self.client.send_file(
                    chat_id,
                    album_files,
                    caption=caption,
                    formatting_entities=entities,
                )
                async with self.stats_lock:
                    self.downloaded_count += 1
                if sent:
                    if isinstance(sent, list):
                        for s in sent:
                            await self.forwarder.forward_media(chat_id, s.id)
                    else:
                        await self.forwarder.forward_media(chat_id, sent.id)
            else:
                total = len(downloaded)
                for i, (msg, dl_path) in enumerate(downloaded):
                    upload_progress = Progress(
                        self.client, chat_id, status_msg.id,
                        f"Uploading {i+1}/{total}",
                    )
                    if msg.video:
                        await self._send_video(
                            chat_id, msg, dl_path, status_msg, upload_progress
                        )
                    elif msg.audio:
                        await self._send_audio(
                            chat_id, msg, dl_path, status_msg, upload_progress
                        )
                    elif msg.photo:
                        await self._send_generic(
                            chat_id, msg, dl_path, status_msg, upload_progress
                        )
                    elif msg.document:
                        mime = getattr(msg.document, "mime_type", "") or ""
                        if mime.startswith("video/"):
                            await self._send_video(
                                chat_id, msg, dl_path, status_msg, upload_progress
                            )
                        elif mime.startswith("audio/"):
                            await self._send_audio(
                                chat_id, msg, dl_path, status_msg, upload_progress
                            )
                        else:
                            info = get_media_info(dl_path)
                            if info:
                                dur = get_media_duration(info)
                                w, h = get_media_dimensions(info, dl_path)
                                if dur and w and h:
                                    await self._send_video(
                                        chat_id, msg, dl_path, status_msg, upload_progress
                                    )
                                elif dur:
                                    await self._send_audio(
                                        chat_id, msg, dl_path, status_msg, upload_progress
                                    )
                                else:
                                    await self._send_generic(
                                        chat_id, msg, dl_path, status_msg, upload_progress,
                                        force_document=True,
                                    )
                            else:
                                await self._send_generic(
                                    chat_id, msg, dl_path, status_msg, upload_progress,
                                    force_document=True,
                                )
                    else:
                        await self._send_generic(
                            chat_id, msg, dl_path, status_msg, upload_progress
                        )

            await status_msg.edit("<b>Media group uploaded.</b>", parse_mode="html")
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Media group error: {e}")
            await status_msg.edit(
                f"<b>Error processing media group:</b> {html.escape(str(e))}",
                parse_mode="html",
            )
            return False
        finally:
            for _, path in downloaded:
                cleanup_download(path)

    async def _download_and_send_media(self, chat_id, message, status_msg, entity):
        if message.file and not is_within_upload_limit(message.file.size):
            await status_msg.edit(
                f"<b>File too large.</b> Size: <code>{format_bytes(message.file.size)}</code> "
                f"exceeds the 2 GB upload limit.",
                parse_mode="html",
            )
            return False

        dl_path = None
        try:
            progress = Progress(self.client, chat_id, status_msg.id, "Downloading")
            filename = get_file_name(message)
            path = get_download_path(chat_id, message.id, filename)
            dl_path = await self._getter.download_media(
                message, file=path, progress_callback=progress
            )

            if not dl_path:
                await status_msg.edit("<b>Download failed.</b>", parse_mode="html")
                return False

            await progress.finish()

            upload_progress = Progress(self.client, chat_id, status_msg.id, "Uploading")

            if message.gif:
                return await self._send_animation(
                    chat_id, message, dl_path, status_msg, upload_progress
                )
            if message.video:
                return await self._send_video(
                    chat_id, message, dl_path, status_msg, upload_progress
                )
            if message.audio:
                return await self._send_audio(
                    chat_id, message, dl_path, status_msg, upload_progress
                )
            if message.document:
                mime = getattr(message.document, "mime_type", "") or ""
                if mime.startswith("video/"):
                    return await self._send_video(
                        chat_id, message, dl_path, status_msg, upload_progress
                    )
                if mime.startswith("audio/"):
                    return await self._send_audio(
                        chat_id, message, dl_path, status_msg, upload_progress
                    )
                info = get_media_info(dl_path)
                if info:
                    dur = get_media_duration(info)
                    w, h = get_media_dimensions(info, dl_path)
                    if dur and w and h:
                        return await self._send_video(
                            chat_id, message, dl_path, status_msg, upload_progress
                        )
                    if dur:
                        return await self._send_audio(
                            chat_id, message, dl_path, status_msg, upload_progress
                        )
                return await self._send_generic(
                    chat_id, message, dl_path, status_msg, upload_progress,
                    force_document=True,
                )
            return await self._send_generic(
                chat_id, message, dl_path, status_msg, upload_progress
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Download/send error: {e}")
            await status_msg.edit(
                f"<b>Error:</b> {html.escape(str(e))}", parse_mode="html"
            )
            return False
        finally:
            if dl_path:
                cleanup_download(dl_path)

    async def _send_generic(self, chat_id, message, dl_path, status_msg, progress,
                            force_document=False):
        caption, entities = prepare_caption(message)
        sent = await self.client.send_file(
            chat_id, dl_path,
            caption=caption,
            formatting_entities=entities,
            force_document=force_document,
            progress_callback=progress,
        )
        async with self.stats_lock:
            self.downloaded_count += 1
        if sent:
            await self.forwarder.forward_media(chat_id, sent.id)
        await status_msg.edit("<b>Uploaded.</b>", parse_mode="html")
        return True

    def _extract_video_attrs(self, message):
        doc = message.video or message.document
        if doc:
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeVideo):
                    return attr.duration, attr.w, attr.h
        return None, None, None

    async def _send_video(self, chat_id, message, dl_path, status_msg, progress):
        info = get_media_info(dl_path)
        duration = get_media_duration(info)
        width, height = get_media_dimensions(info, dl_path)

        if not duration or not width or not height:
            attr_dur, attr_w, attr_h = self._extract_video_attrs(message)
            if not duration:
                duration = attr_dur
            if not width:
                width = attr_w
            if not height:
                height = attr_h

        if not duration:
            duration = 0
        if not width:
            width = 640
        if not height:
            height = 480

        thumb_path = get_thumb_path(chat_id, message.id)
        thumb = generate_video_thumbnail(dl_path, thumb_path)

        try:
            caption, entities = prepare_caption(message)
            attributes = [DocumentAttributeVideo(
                duration=duration,
                w=width,
                h=height,
                supports_streaming=True,
            )]
            kwargs = dict(
                caption=caption,
                formatting_entities=entities,
                attributes=attributes,
                thumb=thumb,
                force_document=False,
                progress_callback=progress,
            )

            sent = await self.client.send_file(chat_id, dl_path, **kwargs)
        finally:
            if thumb and os.path.exists(thumb):
                try:
                    os.remove(thumb)
                except OSError:
                    pass

        async with self.stats_lock:
            self.downloaded_count += 1
        if sent:
            await self.forwarder.forward_media(chat_id, sent.id)
        await status_msg.edit("<b>Uploaded.</b>", parse_mode="html")
        return True

    async def _send_animation(self, chat_id, message, dl_path, status_msg, progress):
        attributes = list(message.document.attributes) if message.document else None
        caption, entities = prepare_caption(message)

        sent = await self.client.send_file(
            chat_id,
            dl_path,
            caption=caption,
            formatting_entities=entities,
            attributes=attributes,
            mime_type=getattr(message.document, "mime_type", "video/mp4"),
            progress_callback=progress,
        )
        async with self.stats_lock:
            self.downloaded_count += 1
        if sent:
            await self.forwarder.forward_media(chat_id, sent.id)
        await status_msg.edit("<b>Uploaded.</b>", parse_mode="html")
        return True

    async def _send_audio(self, chat_id, message, dl_path, status_msg, progress):
        info = get_media_info(dl_path)
        duration = get_media_duration(info)
        artist, title = get_audio_tags(info)
        caption, entities = prepare_caption(message)

        kwargs = dict(
            caption=caption,
            formatting_entities=entities,
            progress_callback=progress,
        )
        if duration:
            kwargs["duration"] = duration
        if artist:
            kwargs["performer"] = artist
        if title:
            kwargs["title"] = title

        sent = await self.client.send_file(chat_id, dl_path, **kwargs)
        async with self.stats_lock:
            self.downloaded_count += 1
        if sent:
            await self.forwarder.forward_media(chat_id, sent.id)
        await status_msg.edit("<b>Uploaded.</b>", parse_mode="html")
        return True

    # ------------------------------------------------------------------
    # Batch Download
    # ------------------------------------------------------------------

    async def _handle_batch_download(self, chat_id, start_url, end_url):
        e1, m1, _ = parse_tg_url(start_url)
        e2, m2, _ = parse_tg_url(end_url)

        if not e1 or not m1 or not e2 or not m2:
            await self.client.send_message(chat_id, "Invalid URLs for batch download.")
            return

        if e1 != e2:
            await self.client.send_message(chat_id, "Both URLs must be from the same chat.")
            return

        if m1 >= m2:
            await self.client.send_message(chat_id, "Start message ID must be less than end message ID.")
            return

        if m2 - m1 > 500:
            await self.client.send_message(chat_id, "Batch range too large (max 500 posts).")
            return

        async with self.task_lock:
            if self.cleanup_in_progress:
                await self.client.send_message(
                    chat_id, "<b>Cleanup in progress, please retry shortly.</b>",
                    parse_mode="html",
                )
                return
            task = asyncio.create_task(self._batch_download_task(chat_id, e1, m1, m2))
            self.running_tasks.add(task)

    async def _batch_download_task(self, chat_id, entity, start_id, end_id):
        total_posts = end_id - start_id + 1
        summary_msg = await self.client.send_message(
            chat_id,
            f"<b>Batch download</b> <code>{entity}</code> ({total_posts} posts)\n"
            f"Progress: <code>0/{total_posts}</code>",
            parse_mode="html",
        )

        downloaded = 0
        skipped = 0
        failed = 0
        processed_groups = set()

        try:
            for msg_id in range(start_id, end_id + 1):
                current = msg_id - start_id + 1
                async with self.semaphore:
                    try:
                        message = await self._getter.get_messages(entity, ids=msg_id)
                        if not message:
                            skipped += 1
                            await self._update_batch_summary(summary_msg, entity, current, total_posts, downloaded, skipped, failed)
                            continue

                        group_key = None
                        if message.grouped_id:
                            group_key = (entity, message.grouped_id)
                            if group_key in processed_groups:
                                skipped += 1
                                await self._update_batch_summary(summary_msg, entity, current, total_posts, downloaded, skipped, failed)
                                continue
                            group = await self._get_media_group(entity, message.id, message.grouped_id)
                            if group:
                                processed_groups.add(group_key)

                        if not message.media and not message.text:
                            skipped += 1
                            await self._update_batch_summary(summary_msg, entity, current, total_posts, downloaded, skipped, failed)
                            continue

                        progress_msg = await self.client.send_message(
                            chat_id,
                            f"<b>Post</b> <code>{msg_id}</code> \u2014 downloading...",
                            parse_mode="html",
                        )

                        if group_key and len(group) > 1:
                            ok = await self._process_media_group(chat_id, group, progress_msg)
                            if ok:
                                downloaded += 1
                            else:
                                failed += 1
                        elif message.media:
                            ok = await self._download_and_send_media(
                                chat_id, message, progress_msg, entity
                            )
                            if ok:
                                downloaded += 1
                            else:
                                failed += 1
                        elif message.text:
                            await self.client.send_message(
                                chat_id,
                                message.message or "",
                                formatting_entities=list(message.entities)
                                    if message.entities else None,
                            )
                            async with self.stats_lock:
                                self.downloaded_count += 1
                            downloaded += 1
                            await progress_msg.edit(
                                f"<b>Post</b> <code>{msg_id}</code> \u2713 Text",
                                parse_mode="html",
                            )

                        await self._update_batch_summary(summary_msg, entity, current, total_posts, downloaded, skipped, failed)
                        await asyncio.sleep(0.5)

                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"Batch error at msg {msg_id}: {e}")
                        failed += 1

                if current % max(1, 10) == 0:
                    await asyncio.sleep(config.FLOOD_WAIT_DELAY)

        except asyncio.CancelledError:
            await summary_msg.edit("<b>Batch download cancelled.</b>", parse_mode="html")
            return
        except Exception as e:
            logger.error(f"Batch task error: {e}")
        finally:
            async with self.task_lock:
                self.running_tasks.discard(asyncio.current_task())

        await summary_msg.edit(
            f"<b>Batch complete</b> <code>{entity}</code>\n"
            f"Downloaded: <code>{downloaded}</code>\n"
            f"Skipped: <code>{skipped}</code>\n"
            f"Failed: <code>{failed}</code>",
            parse_mode="html",
        )

    async def _update_batch_summary(self, summary_msg, entity, current, total, downloaded, skipped, failed):
        now = datetime.now().timestamp()
        if now - self._last_summary_update < 3 and current < total:
            return
        self._last_summary_update = now
        await summary_msg.edit(
            f"<b>Batch</b> <code>{entity}</code> "
            f"Progress: <code>{current}/{total}</code> "
            f"(ok: {downloaded}, skip: {skipped}, fail: {failed})",
            parse_mode="html",
        )
