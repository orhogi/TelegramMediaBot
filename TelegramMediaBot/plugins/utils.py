import copy
import os
import re
import shutil

from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

DOWNLOAD_DIR = "downloads"
ASSETS_DIR = "assets"
CAPTION_LIMIT = 1024


def format_bytes(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def format_time(seconds):
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def get_download_path(chat_id, message_id, filename=None):
    path = os.path.join(DOWNLOAD_DIR, str(chat_id), str(message_id))
    os.makedirs(path, exist_ok=True)
    if filename:
        return os.path.join(path, filename)
    return path


def get_thumb_path(chat_id, message_id):
    os.makedirs(ASSETS_DIR, exist_ok=True)
    return os.path.join(ASSETS_DIR, f"thumb_{chat_id}_{message_id}.jpg")


def cleanup_download(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
        parent = os.path.dirname(path)
        while parent and os.path.basename(parent) and parent not in (DOWNLOAD_DIR, ASSETS_DIR):
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
                parent = os.path.dirname(parent)
            else:
                break
    except OSError:
        pass


def prepare_caption(message):
    text = getattr(message, "message", None) or ""
    entities = list(message.entities) if getattr(message, "entities", None) else None

    if not text:
        return None, None

    if len(text) <= CAPTION_LIMIT:
        return text, entities

    truncated = text[: CAPTION_LIMIT - 1] + "…"
    if not entities:
        return truncated, None

    new_entities = []
    cap = len(truncated)
    for entity in entities:
        if entity.offset >= cap:
            continue
        if entity.offset + entity.length > cap:
            new_entity = copy.copy(entity)
            new_entity.length = cap - entity.offset
            new_entities.append(new_entity)
        else:
            new_entities.append(entity)
    return truncated, new_entities or None


def is_within_upload_limit(size_bytes, is_premium=False):
    if not size_bytes:
        return True
    return size_bytes <= file_size_limit(is_premium)


def clean_all_downloads():
    removed = 0
    freed = 0
    for directory in (DOWNLOAD_DIR, ASSETS_DIR):
        try:
            if os.path.isdir(directory):
                for root, dirs, files in os.walk(directory):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            freed += os.path.getsize(fp)
                            os.remove(fp)
                            removed += 1
                        except OSError:
                            pass
                shutil.rmtree(directory, ignore_errors=True)
        except Exception:
            pass
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        os.makedirs(ASSETS_DIR, exist_ok=True)
    except Exception:
        pass
    return removed, freed


def get_file_name(message):
    if message.file and message.file.name:
        return message.file.name
    if message.media:
        if hasattr(message.media, "document"):
            for attr in message.media.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    return attr.file_name
    if message.photo or isinstance(getattr(message, "media", None), MessageMediaPhoto):
        return f"{message.id}.jpg"
    if message.video or isinstance(getattr(message, "media", None), MessageMediaDocument):
        return f"{message.id}.mp4"
    return f"{message.id}"


def file_size_limit(is_premium=False):
    return 4 * 1024 * 1024 * 1024 if is_premium else 2 * 1024 * 1024 * 1024


def check_file_size_limit(file_path, is_premium=False):
    limit = file_size_limit(is_premium)
    try:
        size = os.path.getsize(file_path)
        return size <= limit
    except OSError:
        return False


def size_of_downloads():
    total = 0
    count = 0
    for directory in (DOWNLOAD_DIR, ASSETS_DIR):
        if os.path.isdir(directory):
            for root, dirs, files in os.walk(directory):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total += os.path.getsize(fp)
                        count += 1
                    except OSError:
                        pass
    return count, total


def parse_story_url(url):
    url = url.strip()
    if url.startswith("t.me/"):
        url = "https://" + url
    elif url.startswith("www.t.me/"):
        url = "https://" + url

    match = re.match(
        r"https?://(?:www\.)?t\.me/([A-Za-z0-9_]{5,32})/s/(\d+)/?$", url
    )
    if match:
        return match.group(1), int(match.group(2))
    return None, None


def parse_tg_url(url):
    url = url.strip()
    if url.startswith("t.me/"):
        url = "https://" + url
    elif url.startswith("www.t.me/"):
        url = "https://" + url

    private_with_thread = re.match(
        r"https?://(?:www\.)?t\.me/c/(-?\d+)/(\d+)/(\d+)", url
    )
    if private_with_thread:
        channel_id = int(private_with_thread.group(1))
        thread_id = int(private_with_thread.group(2))
        msg_id = int(private_with_thread.group(3))
        return _resolve_channel_id(channel_id), msg_id, thread_id

    private_no_thread = re.match(
        r"https?://(?:www\.)?t\.me/c/(-?\d+)/(\d+)", url
    )
    if private_no_thread:
        channel_id = int(private_no_thread.group(1))
        msg_id = int(private_no_thread.group(2))
        return _resolve_channel_id(channel_id), msg_id, None

    public_with_thread = re.match(
        r"https?://(?:www\.)?t\.me/([A-Za-z0-9_]{5,32})/(\d+)/(\d+)", url
    )
    if public_with_thread:
        return public_with_thread.group(1), int(public_with_thread.group(3)), int(public_with_thread.group(2))

    public_no_thread = re.match(
        r"https?://(?:www\.)?t\.me/([A-Za-z0-9_]{5,32})/(\d+)", url
    )
    if public_no_thread:
        return public_no_thread.group(1), int(public_no_thread.group(2)), None

    username_only = re.match(r"https?://(?:www\.)?t\.me/([A-Za-z0-9_]{5,32})$", url)
    if username_only:
        return username_only.group(1), None, None

    return None, None, None


_TELEGRAM_CHANNEL_OFFSET = -1000000000000


def _resolve_channel_id(channel_id):
    if channel_id > 0:
        return _TELEGRAM_CHANNEL_OFFSET - channel_id
    if channel_id > _TELEGRAM_CHANNEL_OFFSET:
        return _TELEGRAM_CHANNEL_OFFSET + channel_id
    return channel_id


def get_media_info(file_path):
    import json
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout)
    except Exception:
        return {}


def get_media_dimensions(info, file_path=None):
    if not info:
        return None, None
    streams = info.get("streams", [])
    for stream in streams:
        if stream.get("codec_type") == "video":
            return stream.get("width"), stream.get("height")
    return None, None


def get_media_duration(info):
    if not info:
        return None
    fmt = info.get("format", {})
    duration = fmt.get("duration")
    if duration and float(duration) > 0:
        return max(1, int(round(float(duration))))
    streams = info.get("streams", [])
    for stream in streams:
        dur = stream.get("duration")
        if dur and float(dur) > 0:
            return max(1, int(round(float(dur))))
    return None


def get_audio_tags(info):
    if not info:
        return None, None
    fmt = info.get("format", {})
    tags = fmt.get("tags", {})
    return tags.get("artist"), tags.get("title")


def generate_video_thumbnail(file_path, output_path):
    import subprocess

    info = get_media_info(file_path)
    duration = get_media_duration(info)
    if not duration:
        return None
    seek = max(1, duration // 2)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-v", "quiet",
                "-ss", str(seek),
                "-i", file_path,
                "-vframes", "1",
                "-q:v", "2",
                "-y",
                output_path,
            ],
            capture_output=True,
            timeout=60,
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
        if os.path.exists(output_path):
            os.remove(output_path)
    except Exception:
        pass
    return None
