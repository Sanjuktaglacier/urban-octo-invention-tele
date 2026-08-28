# Ultimate Telegram Content Saver Bot

Download and forward media from any Telegram channel or group
(public or private) that your account has access to.
No file size restrictions - uses Telethon for uploads up to 2 GB.
Deployable on Railway via Docker.

---

## Features

- /setchannel  -- Set destination channel for forwarded files
- /forward     -- Download one message and forward it
- /test        -- Check if a message is accessible
- /batch       -- Download a range of messages
- /topic       -- List forum topics in a group
- /topic_select -- Select a topic for downloading
- /status      -- Show bot status
- /cancel      -- Cancel running batch
- /history     -- Show download statistics
- NO file size limit -- Telethon uploads files up to 2 GB directly
- Auto ID format correction (handles all -100XXXX variants)
- Retry with exponential backoff
- FloodWait auto-handling
- SQLite persistence (survives Railway restarts)
- Duplicate skip (never downloads same message twice)
- Progress editing (one message, no spam)
- Semaphore-limited concurrency
- Health endpoint at /health for Railway

---

## Upload Strategy

Files are uploaded using Telethon (your user account), not the Bot API.
This means:
  - No 50 MB Bot API limit
  - Supports files up to 2 GB (Telegram's actual limit)
  - Files above 2 GB will be attempted but may fail
  - Progress is tracked during both download and upload

---

## Quick Start

### 1. Get API credentials

Visit https://my.telegram.org/apps
Create a new application and note API_ID and API_HASH.

### 2. Create a bot

Message @BotFather on Telegram and use /newbot.
Copy the bot token.

### 3. Generate session string (run in Google Colab)

```
!pip install telethon -q
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

api_id   = int(input('API_ID: '))
api_hash = input('API_HASH: ').strip()

async def gen():
    c = TelegramClient(StringSession(), api_id, api_hash)
    await c.start()
    print('SESSION_STRING:', c.session.save())
    await c.disconnect()

asyncio.get_event_loop().run_until_complete(gen())
```

### 4. Configure

```
cp .env.example .env
# Fill in all values in .env
```

### 5. Run locally

```
pip install -r requirements.txt
python main.py
```

### 6. Run tests

```
pytest tests/ -v
```

---

## Environment Variables

Required:
  API_ID                   -- Telegram API ID
  API_HASH                 -- Telegram API hash
  SESSION_STRING           -- Telethon session string
  BOT_TOKEN                -- Bot token from @BotFather
  OWNER_ID                 -- Your numeric Telegram user ID

Optional:
  DOWNLOAD_DIR             -- Default: /tmp/downloads
  MAX_CONCURRENT_DOWNLOADS -- Default: 3
  MAX_RETRIES              -- Default: 5
  LOG_LEVEL                -- Default: INFO
  DB_PATH                  -- Default: data/saver.db
  PORT                     -- Default: 8080

---

## Railway Deployment

1. Push this repo to GitHub
2. Go to railway.app and create a new project
3. Select Deploy from GitHub repo
4. Add all environment variables in Railway dashboard
5. Railway builds the Docker image and deploys automatically
6. Check /health endpoint for status

---

## Usage Examples

Set destination channel:
  /setchannel -1001234567890
  /setchannel https://t.me/+xxxxxxxxxxxx

Test a message:
  /test https://t.me/example/123
  /test https://t.me/c/1234567890/42

Forward one message (any size):
  /forward https://t.me/example/123

Batch download (any size files):
  /batch https://t.me/example/100 https://t.me/example/500

Topic download workflow:
  /topic https://t.me/+xxxxxxxxxxxx
  /topic_select 2
  /batch https://t.me/c/GROUP_ID/1 https://t.me/c/GROUP_ID/999

Check status:
  /status

Cancel running batch:
  /cancel

View history:
  /history

---

## Troubleshooting

Bot not responding:
  Check BOT_TOKEN and OWNER_ID.
  Get your user ID from @userinfobot on Telegram.

Access denied:
  Your Telegram account must be a member of the source channel/group.
  Use /setchannel with an invite link to join automatically.

Wrong chat ID format:
  The bot automatically tries multiple ID formats.
  -1004417323799 will also try -1001004417323799.
  To find the correct ID: forward a message to @userinfobot.

Session expired:
  Regenerate SESSION_STRING using the Colab script above.

Large file upload fails:
  Telethon supports up to 2 GB. Files above this cannot be sent
  via Telegram at all regardless of method used.

---

## Security

- Only OWNER_ID can use the bot
- SESSION_STRING is never logged or shown in messages
- .env is excluded from Git via .gitignore
- All file paths are sanitized against traversal attacks

---

## License

MIT
