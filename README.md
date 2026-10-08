# Server Archive Bot

A Discord bot that downloads every attachment posted in the channels you choose.

- Archives images, videos, files and voice messages as soon as they are posted, including attachments of forwarded messages.
- Covers threads and forum posts inside the chosen channels.
- Can scan the full history of those channels once. Later scans only look at new messages.
- Stores each attachment's details in your PostgreSQL database: the message link, author, file name, size and CDN link.
- Retries failed downloads on the next start.

## Discord setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Under **Bot**, copy the token and turn on **Message Content Intent**. Without it, Discord sends messages without their attachments.
3. Under **OAuth2 → URL Generator**, select the `bot` scope and these permissions:
   - View Channels
   - Read Message History
   - Manage Threads (optional, needed to scan private threads that are archived)
4. Open the generated URL to add the bot to your server.

To get a channel ID, turn on **Developer Mode** in Discord's advanced settings, then right-click the channel and choose **Copy Channel ID**.

## Configuration

Copy `.env.example` to `.env` and fill it in:

```env
DISCORD_TOKEN=your-discord-bot-token

# Channels to archive, separated by commas. Threads and forum posts in them are included.
CHANNEL_IDS=123456789012345678,234567890123456789

# Set to true to also scan the full history of every channel on startup.
# Leave it false to only archive new messages.
ARCHIVE_HISTORY=false

DATABASE_URL=postgresql://user:password@host:5432/database

# Docker only. The bot's IP on the br0 network. Pick a free one outside your router's DHCP range.
BOT_IP=192.168.0.50

# Optional. DEBUG, INFO, WARNING or ERROR. Defaults to INFO.
LOG_LEVEL=INFO
```

If the database password contains special characters such as `@`, `:` or `/`, URL-encode them (`@` becomes `%40`).

## Running

### Docker

```sh
mkdir archive
docker compose up -d
```

This pulls the prebuilt image from GHCR. Files are saved to `./archive`; change the volume in `docker-compose.yml` to store them somewhere else. Logs are kept in the `logs` volume.

The container joins the existing `br0` network (Unraid's custom network) with the IP from `BOT_IP`. This lets it reach a database container that has its own IP on `br0`, which containers on the default bridge network cannot.

The bot runs as UID 1000 in the container. If it says it cannot write to the archive folder, give that user the folder with `sudo chown 1000:1000 archive`.

### Windows

Run `run.bat`. It creates a virtual environment and installs the requirements on first run, then starts the bot. Files are saved to the `archive` folder next to `bot.py`.

### Manually

Requires Python 3.12 or newer.

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
.venv/bin/python bot.py                     # Windows: .venv\Scripts\python
```

## The archive

```
archive/
├── general/
│   ├── 1290000000000000001_cat.png
│   └── Some thread/
│       └── 1290000000000000002_video.mp4
└── forum/
    └── Forum post title/
        └── 1290000000000000003_notes.pdf
```

Files are named `<attachment id>_<original name>`. Folders are named after the channel and thread. Characters that are not allowed in file names are replaced with `_`.

## The database

The bot creates its tables on first start.

`attachments` has one row per attachment, keyed by its attachment ID:

| Column | |
|---|---|
| `message_url` | Link to the message in Discord |
| `url` | CDN link to the file, without the signature (see below) |
| `filename`, `content_type`, `size` | As uploaded |
| `channel_id`, `guild_id`, `author_id`, `message_id`, `message_date` | Where and when it was posted, and by whom |
| `file_path` | Path inside the archive folder |
| `downloaded` | Whether the file is on disk |
| `unavailable` | The message or file was gone before it could be downloaded |

`scan_progress` stores how far the history scan got in each channel and thread. Delete a channel's row to scan it again from the start.

### About attachment links

Discord adds an expiring signature (`?ex=…&is=…&hm=…`) to attachment links, and they stop working after about a day. The bot stores the link without it. Discord can sign that link again: the Discord app does this when you paste it, and bots can use the `POST /attachments/refresh-urls` API endpoint.

This only works while Discord still has the file. Discord usually deletes the files of a deleted message, so the downloaded copy is the one that lasts.
