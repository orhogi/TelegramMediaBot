# Telegram Media Bot

A unified Telegram bot that downloads stories and media from any Telegram post — including private/restricted channels. Built with Telethon, runs on any platform.

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/Telegram-@c__0__t__e-blue.svg)](https://t.me/c_0_t_e)

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Getting a Session](#getting-a-session)
- [Command Reference](#command-reference)
  - [Story Download](#story-download)
  - [Post Download](#post-download)
  - [Batch Download](#batch-download)
  - [Session Generator](#session-generator)
  - [Admin Commands](#admin-commands)
  - [Utility Commands](#utility-commands)
- [Docker](#docker)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Features

- **Story Download** — Download all current stories from any Telegram user
- **Post Download** — Download media from any Telegram post (photos, videos, audio, documents, text)
- **Media Groups** — Albums are downloaded and re-uploaded together as a group
- **Batch Download** — Download a range of posts with `/bdl`
- **In-Chat Session Generator** — Generate a user session with `/login` — no external tools needed
- **Progress Bars** — Real-time download/upload progress: percentage, speed, ETA, visual bar
- **Auto-Forward** — Optionally forward all downloads to a target channel
- **System Stats** — CPU, RAM, disk, and network monitoring via `/stats`
- **Dual-Client** — Bot handles interaction, user session accesses restricted content

---

## Prerequisites

- **Python 3.12** or later
- **[FFmpeg](https://ffmpeg.org)** — for video thumbnails (optional but recommended)
- **Telegram API credentials** — get them from [my.telegram.org/apps](https://my.telegram.org/apps)
- **A Telegram bot token** — create one with [@BotFather](https://t.me/BotFather)
- **A user session** — generate it with `/login` inside the bot (see [Getting a Session](#getting-a-session))

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/yourusername/TelegramMediaBot.git
cd TelegramMediaBot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env (see Configuration section)
cp .env.example .env
# Edit .env with your API_ID, API_HASH, TOKEN, DEVS

# 4. Run
python -m TelegramMediaBot
```

**On first run:** DM the bot `/login` to generate your user session. This is required for stories and private posts.

---

## Configuration

Create a `.env` file in the project root. Use `.env.example` as a template.

```env
API_ID=1234567
API_HASH=abcdef1234567890abcdef1234567890
TOKEN=1234567890:ABCDEFghijklmnopqrstuvwxyz12345678
DEVS=1234567890
STRING_SESSION=
FORWARD_CHAT_ID=
MAX_CONCURRENT_DOWNLOADS=1
FLOOD_WAIT_DELAY=10
```

| Variable | Required | Description |
|---|---|---|
| `API_ID` | Yes | Telegram API ID from [my.telegram.org](https://my.telegram.org/apps) |
| `API_HASH` | Yes | Telegram API hash from [my.telegram.org](https://my.telegram.org/apps) |
| `TOKEN` | Yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `DEVS` | Yes | Comma-separated developer user IDs — can use `/status`, `/stats` |
| `STRING_SESSION` | Auto | User session string — leave empty, use `/login` to auto-fill |
| `FORWARD_CHAT_ID` | Optional | Channel ID or `@username` to auto-forward all downloads |
| `MAX_CONCURRENT_DOWNLOADS` | No (1) | Maximum simultaneous downloads |
| `FLOOD_WAIT_DELAY` | No (10) | Seconds between batch chunks to avoid rate limits |

---

## Getting a Session

A **user session** authenticates the bot as your real Telegram account, enabling it to access stories and private channels. The bot client alone cannot do this.

### Option 1: In-Chat `/login` (Recommended)

1. Start the bot: `python -m TelegramMediaBot`
2. DM the bot: `/login`
3. Bot asks for your phone number → send it in international format: `+1234567890`
4. Bot sends a verification code to your Telegram app
5. Send the code **with spaces** (e.g. `1 2 3 4 5`) — this prevents Telegram from auto-deleting the message. The bot strips spaces automatically.
6. If you have 2FA enabled, enter your password when prompted
7. Bot replies with your session string — copy it into `.env` as `STRING_SESSION`
8. Restart the bot

> Note: Codes expire in ~60 seconds. Respond quickly, or use `/cancel` to retry.

### Option 2: External Tools

If `/login` doesn't work, use these alternatives to generate a Telethon session string:

- [@genStr_robot](https://t.me/genStr_robot) on Telegram
- [Telethon session generator script](https://docs.telethon.dev/en/stable/basic/signing-in.html)

Then paste the string into `STRING_SESSION` in your `.env` file.

---

## Command Reference

All commands work in **private chat** with the bot only.

### Story Download

Download all current stories from a Telegram user.

| Usage | Example |
|---|---|
| `@username` | `@durov` |
| `t.me/username` | `https://t.me/durov` |

**What happens:**
1. Bot sends "Fetching stories from durov..."
2. For each story, a progress message appears:
   ```
   Story 1/3 from durov
   ██████████░░ 85%  12.3 MB / 14.5 MB  Speed: 2.1 MB/s  ETA: 1s
   ```
3. After upload, the message updates to `Story 1/3 ✓ Complete`
4. Each subsequent story appears **below** the previous one
5. Final summary: "All 3 stories from durov uploaded."

**Requirements:** User session required (`/login`). Without one, the bot will tell you to use `/login`.

**Supported media:** Photos, videos, and documents from stories. Captions are preserved.

---

### Post Download

Download media from any Telegram post — public channels, private channels (with session), or groups.

| Usage | Example |
|---|---|
| Paste a post link | `https://t.me/channel/123` |
| `/dl <url>` | `/dl https://t.me/channel/123` |

**Accepted link formats:**
- `https://t.me/username/123` — public channel/group post
- `https://t.me/c/1234567890/456` — private channel post
- `t.me/username/123` — without protocol (auto-detected)

**What happens:**
1. Bot sends "Fetching post channel/123..."
2. Downloads the media with progress bar
3. Uploads with progress bar
4. Message shows "Uploaded." when complete
5. If auto-forward is configured, the media is also forwarded

**Media types handled:**

| Type | How it's sent |
|---|---|
| Photo | As photo, original quality |
| Video | With streaming support, correct resolution and duration |
| Audio | With performer/title tags |
| Document | As document (`force_document=True`) |
| Video as document (MP4 file) | Detected via mime type, sent as streamable video |
| Text-only post | Copied as a text message |
| Media group / Album | All items downloaded and re-uploaded as a group |
| Poll | "Polls cannot be downloaded" message |

**Requirements:** Public posts work without a session (bot client). Private posts require a user session (`/login`).

---

### Batch Download

Download a range of posts from the same channel.

```
/bdl <start_url> <end_url>
```

| Example |
|---|
| `/bdl https://t.me/channel/100 https://t.me/channel/120` |

**Rules:**
- Both URLs must be from the **same chat**
- Start message ID must be **less than** end message ID
- Range is inclusive (100 to 120 = 21 posts)

**What happens:**
1. A **summary counter** appears at the top:
   ```
   Batch download @channel (21 posts)  Progress: 0/21
   ```
2. For each post, a new progress message appears **below** previous ones:
   ```
   Post 100 — downloading...
   ██████████░░ 100% ✓
   Post 101 — downloading...
   ██████░░░░░░ 52%  8.3 MB / 16.0 MB
   ```
3. The summary counter updates in real-time:
   ```
   Batch @channel Progress: 5/21 (ok: 4, skip: 1, fail: 0)
   ```
4. Final summary: "Batch complete @channel — Downloaded: 18, Skipped: 2, Failed: 1"
5. A configurable flood wait delay (`FLOOD_WAIT_DELAY`) pauses between chunks of 10 posts

**Requirements:** Same as post download — public works without session, private needs session.

---

### Session Generator

Generate a user session string without leaving Telegram.

| Command | Description |
|---|---|
| `/login` | Start session generation |
| `/cancel` | Abort at any step (only during login) |

**Full flow:**

```
You:    /login
Bot:    Session Generator — Send your phone number (+1234567890)

You:    +1234567890
Bot:    Code sent. Send it with spaces (e.g. 1 2 3 4 5)

You:    1 2 3 4 5
Bot:    [if 2FA] 2FA detected. Enter your password.
You:    mypassword

Bot:    Session generated:
        1BQANOTEuMTAuMTA4LjE1MC4yMD...
        Copy this to STRING_SESSION in .env and restart.
```

**Edge cases:**
- Wrong phone format → "Invalid format. Use +1234567890"
- Wrong code → "Wrong or expired code. Try again."
- Code expired → Same as above — use `/cancel` and start over
- Wrong 2FA password → "Wrong password. Try again."
- `/cancel` at any step → "Login cancelled." (clean disconnect)

**Why spaces?** Telegram may auto-delete messages containing only numbers (anti-spam). Sending with spaces prevents this. The bot strips spaces, dashes, and other separators automatically before submitting.

---

### Admin Commands

Only users listed in `DEVS` can use these.

| Command | Description | Output |
|---|---|---|
| `/status` | Bot statistics | Users, total downloads, active tasks, temp files, uptime |
| `/stats` | System resources | CPU %, RAM used/total, disk used/total, network I/O, uptime |

---

### Utility Commands

| Command | Description |
|---|---|
| `/start` | Welcome message. **Shows login prompt** if no session configured |
| `/help` | Quick command reference |
| `/killall` | Cancel all running download tasks (stories + posts + batch) |
| `/cleanup` | Delete all temp download files — shows count and bytes freed |

---

## Docker

### docker-compose (recommended)

```bash
docker compose up -d
```

### Docker CLI

```bash
docker build -t telegram-media-bot .
docker run -d \
  -v $(pwd)/sessions:/app/sessions \
  -v $(pwd)/.env:/app/.env:ro \
  -v $(pwd)/downloads:/app/downloads \
  telegram-media-bot
```

**Multi-architecture.** The image runs on:
- **x86\_64** — VPS, cloud servers, Intel/AMD desktops
- **arm64** — Apple Silicon, AWS Graviton, Raspberry Pi 4/5
- **arm/v7** — Raspberry Pi 2/3

**Volumes:**
| Mount | Purpose |
|---|---|
| `/app/sessions` | Persists session files across container restarts |
| `/app/.env:ro` | Configuration (read-only mount for security) |
| `/app/downloads` | Temporary download storage |

**Note:** The Dockerfile installs `ffmpeg` automatically. No additional setup needed.

---

## Project Structure

```
.
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── .dockerignore
└── TelegramMediaBot/
    ├── __init__.py
    ├── __main__.py              # Entry point
    └── plugins/
        ├── __init__.py
        ├── config.py            # Environment variable loader
        ├── handler.py           # Main bot logic (all commands + downloads)
        ├── progress.py          # Custom async progress bar
        ├── forward.py           # Auto-forward to target chat
        └── utils.py             # URL parser, file helpers, ffprobe/ffmpeg wrappers
```

---

## Troubleshooting

### Startup Issues

| Symptom | Fix |
|---|---|
| Bot asks for phone number on terminal | No `STRING_SESSION` and no `userbot.session` file. Start the bot, DM it `/login`, copy the session string to `.env`, restart. |
| `ConfigError: API_ID is not set` | Your `.env` file is missing or has wrong keys. Copy `.env.example` to `.env` and fill in your credentials. |
| `Invalid STRING_SESSION` | The `STRING_SESSION` in `.env` is not a valid Telethon session string. Use `/login` to generate a new one. |
| Docker: bot immediately exits | Check logs: `docker compose logs bot`. Usually a missing `.env` file or bad credentials. |

### Story Downloads

| Symptom | Fix |
|---|---|
| "Story downloads require a valid user session" | Use `/login` to generate a session. Stories cannot be accessed by bot accounts. |
| "No stories found" | The user has no current stories, or all stories have expired. |
| Story download fails mid-way | Story may have expired between fetch and download. Rare — try again. |

### Post Downloads

| Symptom | Fix |
|---|---|
| "Failed to fetch post — may not be accessible" | The bot doesn't have access. Public posts work without session. For private posts, use `/login`. |
| "Post not found" | The message ID doesn't exist or the link is malformed. Verify the URL. |
| Videos sent but can't play / wrong resolution | ffmpeg may not be installed. Install it: `apt install ffmpeg` (Linux), `brew install ffmpeg` (macOS), or download from [ffmpeg.org](https://ffmpeg.org) (Windows). |
| PDFs or non-media documents fail | Only media types are supported. Text-only posts are copied as text. |

### Login Flow

| Symptom | Fix |
|---|---|
| Code expired before you can send it | Telegram codes expire in ~60s. Use `/cancel` and `/login` again, respond faster. |
| Code rejected even though it's correct | Send the code **with spaces** (`1 2 3 4 5`). Telegram sometimes auto-deletes plain-number messages. The bot strips spaces automatically. |
| "Wrong or expired code" repeatedly | Wait 1-2 minutes before trying again. Telegram rate-limits code requests. |
| `/cancel` doesn't work during login | Fixed — `/cancel` is now detected at any login step. |
| 2FA prompt, then "Wrong password" | Enter your Telegram cloud password (not your login code). If forgotten, reset it via Telegram settings. |
| Login succeeds but session doesn't work after restart | You need to copy the session string into `.env` as `STRING_SESSION` and restart the bot. The string is only shown once. |

### Batch Downloads

| Symptom | Fix |
|---|---|
| "Invalid URLs" | Check that both URLs are valid post links (contain a message ID). Usernames and profile links won't work. |
| "Both URLs must be from the same chat" | The start and end URLs must point to the same channel/group. |
| Batch hangs or is very slow | `FLOOD_WAIT_DELAY` pauses every 10 posts. Lower it in `.env` — but too low risks rate limits. |
| Some posts skipped | The post has no media or text, OR it's part of a media group already processed. |

---

## License

MIT License. See [LICENSE](LICENSE) for details.
