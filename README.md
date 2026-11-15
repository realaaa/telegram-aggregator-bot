# Telegram Aggregator Bot

A personal Telegram bot that collects messages from public Telegram channels, clusters them into topics using Claude, fact-checks key claims with Claude's built-in web search, and delivers a condensed digest every morning (or on demand).

## How It Works

1. You subscribe to public Telegram channels via bot commands.
2. Every morning at `DIGEST_TIME` (or whenever you send `/summary`), the bot fetches the last 24 hours of messages from all subscribed channels.
3. Messages are sent to Claude (Sonnet 5.5), which clusters them into topics, categorises them, and extracts key claims — returned as schema-validated JSON.
4. Claude checks the claims against current web sources using its built-in web search tool.
5. The digest — with links to the original posts and fact-check sources — is published as a [Telegra.ph](https://telegra.ph) page, and the bot sends you just the link (opens with Instant View in Telegram). If publishing fails, the full digest is sent as Telegram messages instead.

> **Privacy:** Telegra.ph pages are public to anyone who has the link (they are not listed or searchable by default). The bot creates a Telegraph account on first use and stores its token in `DATA_DIR/telegraph.json`.

## Prerequisites

- **Python 3.11+** (local) or **Docker** (server)
- **Telegram account** (for the MTProto user client that reads channels)
- **API keys** (see below)

## Credentials

You need three sets of credentials:

#### Telegram Bot Token

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts.
3. Copy the bot token.

#### Your Telegram User ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram.
2. It will reply with your user ID (a number like `123456789`).

#### Telegram API ID & Hash (for Telethon)

1. Go to [https://my.telegram.org](https://my.telegram.org).
2. Log in with your phone number.
3. Go to **API development tools**.
4. Create a new application (name and platform don't matter).
5. Copy the **App api_id** and **App api_hash**.

#### Anthropic API Key

> **Important:** This bot uses the **Claude API platform**, not the claude.ai consumer chat.
> These are separate products with separate billing. A Claude Pro subscription does **not** cover API usage.

1. Go to [https://platform.claude.com](https://platform.claude.com).
2. Add billing / credits — this is what the bot consumes (tokens + web searches).
3. Create an API key.
4. Web search must be enabled for your organisation in the Claude Console settings.

## Configuration

Create `.env` in the project root:

```bash
# Telegram Bot API token (from @BotFather)
BOT_TOKEN=your_bot_token_here

# Your personal Telegram user ID
OWNER_ID=123456789

# Telegram API credentials (from https://my.telegram.org)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash_here

# Anthropic API key
ANTHROPIC_API_KEY=sk-ant-...

# --- Optional ---
# Daily digest time (HH:MM, 24h) and timezone; DIGEST_TIME=off disables it
DIGEST_TIME=07:00
DIGEST_TIMEZONE=Australia/Sydney
# Claude model (default: claude-sonnet-5-5)
CLAUDE_MODEL=claude-sonnet-5-5
# Where data.db and user_session.session live (default: project root; /data in Docker)
DATA_DIR=

# --- Optional: profile (for a public news channel bot) ---
# Post scheduled digests to this channel instead of your DMs (bot must be a channel admin)
DIGEST_CHAT=@your_channel
# Weekly roundup right after the daily digest on this day (mon..sun, default off)
WEEKLY_DAY=mon
# Topic focus: off-topic posts are skipped
DIGEST_FOCUS="IT professionals: web hosting, MSPs, cloud, cybersecurity"
# Comma-separated categories ("Other" is always added)
DIGEST_CATEGORIES=Security,Vulnerabilities,Cloud & Hosting,MSP & Industry,Vendors & Products,Tools
# Page titles and Telegraph author branding
DIGEST_TITLE=Daily Digest
WEEKLY_TITLE=Weekly Roundup
BRAND_NAME=News Aggregator Bot
BRAND_URL=https://example.com
```

Scheduled digests post a short teaser (top 3 headlines + link) to `DIGEST_CHAT`, or to you if it's unset. `/summary` and `/weekly` always send a preview to you only. Empty results and errors never go to the channel — you get them privately, along with any channels that couldn't be read.

The weekly roundup is built from the **scheduled** daily digests of the past week (stored in the DB for 30 days), merging stories across days and carrying over their sources and fact-checks — no extra web searches. The first roundup covers only the days since this feature was deployed.

## Running Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m bot
```

### First run — Telethon authentication

On the first run, Telethon prompts you in the terminal to log in to your **personal** Telegram user account (phone number, code, and 2FA password if enabled). **Never enter the bot token here** — bot accounts cannot read channel history (`BotMethodInvalidError`). Telegram ends sessions after ~6 months of inactivity by default, so expect to log in again after long breaks. This creates `user_session.session` in `DATA_DIR`. **Do not share this file** — it grants full access to your Telegram account.

## Deploying with Docker (VPS)

The bot uses long polling — it needs **no open ports, domain or reverse proxy**, only outbound HTTPS. All state lives in `./data` (SQLite DB + Telethon session).

> Only one instance may run per bot token — stop the local copy before starting the server one.

```bash
# On the server (containers live under /opt/stack)
cd /opt/stack
git clone github-tgbot:realaaa/telegram-aggregator-bot.git   # private repo: see "Deploy key" below
cd telegram-aggregator-bot

# Secrets: create .env (see Configuration) and lock it down
chmod 600 .env

# Data dir, owned by the container user (uid 1001 - see `ARG UID` in Dockerfile)
mkdir -p data && chown 1001:1001 data && chmod 700 data
```

Bring over your existing state from your Mac (optional — keeps channel list and Telegram login):

```bash
# On your Mac
scp data.db user_session.session telegraph.json <user>@web.badran.com.au:/opt/stack/telegram-aggregator-bot/data/
# On the server
chown 1001:1001 data/* && chmod 600 data/*
```

Or log in fresh inside the container (interactive, one-off):

```bash
docker compose build
docker compose run --rm -it bot      # enter phone/code, then Ctrl+C once "Bot starting" appears
```

Start it:

```bash
docker compose up -d --build
docker compose logs -f
```

Update to a new version:

```bash
git pull && docker compose up -d --build
```

### Multiple bots (one per audience)

`compose.yaml` runs one container per bot from the same image. Each has its own `.env` file and data dir:

| Service | Env file | Data dir | Purpose |
|---|---|---|---|
| `bot` | `.env` | `./data` | Personal digest in your DMs |
| `badrantech` | `.env.badrantech` | `./data-badrantech` | Badran Tech news channel |

Setting up the Badran Tech bot:

1. Create a new bot with @BotFather and a Telegram channel; add the bot to the channel as an **admin** (with "Post messages").
2. On the server:
   ```bash
   cd /opt/stack/telegram-aggregator-bot
   cp .env .env.badrantech && chmod 600 .env.badrantech
   # edit .env.badrantech: new BOT_TOKEN, DIGEST_TIME=06:30, DIGEST_CHAT=@<channel>,
   #   WEEKLY_DAY=mon, DIGEST_FOCUS, DIGEST_CATEGORIES, DIGEST_TITLE, WEEKLY_TITLE,
   #   BRAND_NAME="Badran Tech", BRAND_URL=https://badran.com.au
   mkdir -p data-badrantech && chown 1001:1001 data-badrantech && chmod 700 data-badrantech
   docker compose build
   docker compose run --rm -it badrantech   # log in to Telegram (phone + code), Ctrl+C after "Bot starting"
   docker compose up -d
   ```
3. Message the new bot: `/add_channel ...` for each source channel, then `/summary` to preview.

Each bot logs in to Telegram separately — **never copy a `user_session.session` between data dirs**: the same session used from two places at once gets revoked. To add another bot, copy the `badrantech` service with a new name, env file and data dir.

### Deploy key (private repo)

```bash
ssh-keygen -t ed25519 -C "vps-telegram-aggregator-bot" -f ~/.ssh/tg_bot_deploy -N ""
cat ~/.ssh/tg_bot_deploy.pub   # add at GitHub repo -> Settings -> Deploy keys (read-only)
cat >> ~/.ssh/config <<'CFG'
Host github-tgbot
    HostName github.com
    User git
    IdentityFile ~/.ssh/tg_bot_deploy
    IdentitiesOnly yes
CFG
ssh -T github-tgbot
```

To use a different host UID, change `ARG UID` in the Dockerfile (or add `build: {context: ., args: {UID: "1234"}}` in `compose.yaml`) and `chown` `data/` to match.

Back up `data/` (contains your Telegram session and Telegraph token — treat as secrets).

## Usage

| Command | Description |
|---|---|
| `/start` | Welcome message and digest schedule |
| `/add_channel <username>` | Subscribe to a public channel (validated) |
| `/remove_channel <username>` | Unsubscribe from a channel |
| `/list_channels` | Show all subscribed channels |
| `/summary` | Preview a digest of the last 24 hours (sent to you only) |
| `/weekly` | Preview the weekly roundup from the past week's daily digests |
| `/help` | Show available commands |

## Project Structure

```
telegram-aggregator-bot/
├── bot/
│   ├── __main__.py        # Entry point — starts aiogram, Telethon, daily scheduler
│   ├── config.py          # Environment-based configuration
│   ├── database.py        # SQLite storage (channels & messages)
│   ├── channel_reader.py  # Telethon MTProto client — reads channels
│   ├── summarizer.py      # Claude — topic clustering & summarisation (structured outputs)
│   ├── fact_checker.py    # Claude + web search — claim verification
│   ├── formatter.py       # Report formatting for Telegram HTML
│   ├── digest.py          # Digest pipeline + daily scheduler
│   ├── telegraph.py       # Telegra.ph publishing
│   └── handlers.py        # aiogram command handlers
├── Dockerfile
├── compose.yaml
├── requirements.txt
├── PRD.md
└── README.md
```

## Troubleshooting

### "Missing required environment variable"

Make sure `.env` exists and all required variables are set.

### "Telethon session is not authorised"

The bot is running non-interactively without a valid session. Copy `user_session.session` into `data/`, or run `docker compose run --rm -it bot` once to log in.

### Bot doesn't respond to commands

- Verify `OWNER_ID` matches your actual Telegram user ID.
- Make sure you started a conversation with the bot first (send `/start`).
- Make sure only one instance is running for this bot token.

### "Couldn't find a public channel"

- The channel must be **public** and the username spelled correctly (without `@`).

### Costs

Each digest is one summarisation call plus one fact-check session (up to 25 web searches), per bot. A weekly roundup is one extra summarisation call over the stored daily topics (no web searches). Check usage in the Claude Console.
