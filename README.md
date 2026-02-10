# Telegram Aggregator Bot

A personal Telegram bot that collects messages from public Telegram channels, clusters them into topics using Claude AI, fact-checks key claims via web search, and delivers a condensed digest on demand.

## How It Works

1. You subscribe to public Telegram channels via bot commands.
2. When you send `/summary`, the bot fetches the last 24 hours of messages from all subscribed channels.
3. Messages are sent to Claude AI, which clusters them into topics, categorises them, and identifies key claims.
4. Claims are fact-checked against the web using Tavily search.
5. A formatted digest is delivered back to you in Telegram.

## Prerequisites

- **macOS** (tested on macOS Ventura+)
- **Python 3.11+**
- **Telegram account** (for the MTProto user client that reads channels)
- **API keys** (see [Configuration](#configuration) below)

### Install Python

If you don't have Python 3.11+ installed, use Homebrew:

```bash
brew install python@3.12
```

Verify:

```bash
python3 --version
```

## Setup

### 1. Clone and create virtual environment

```bash
cd ~/Coding/telegram-aggregator-bot
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Obtain API credentials

You'll need four sets of credentials:

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

> **Important:** This bot uses the **Anthropic API platform**, not the claude.ai consumer chat.
> These are separate products with separate billing. A Claude Pro subscription (claude.ai) does **not** cover API usage.
> You need credits on the **API platform** at [console.anthropic.com](https://console.anthropic.com).

1. Go to [https://console.anthropic.com](https://console.anthropic.com).
2. Create an account or sign in.
3. Add billing / credits under **Plans & Billing** — this is what the bot consumes.
4. Navigate to **API Keys** and create a new key.

#### Tavily API Key

1. Go to [https://tavily.com](https://tavily.com).
2. Sign up for an account.
3. Copy your API key from the dashboard.

## Configuration

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

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

# Tavily API key
TAVILY_API_KEY=tvly-...
```

## Running the Bot

```bash
source .venv/bin/activate
python -m bot
```

### First run — Telethon authentication

On the first run, Telethon will prompt you in the terminal to authenticate your Telegram user account:

1. Enter your phone number (international format, e.g. `+1234567890`).
2. Enter the verification code Telegram sends you.
3. If you have 2FA enabled, enter your password.

This creates a `user_session.session` file locally. **Do not share this file** — it grants access to your Telegram account.

Subsequent runs will use the saved session automatically.

### Usage

Once the bot is running, open your bot in Telegram and use these commands:

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/add_channel <username>` | Subscribe to a channel (e.g. `/add_channel durov`) |
| `/remove_channel <username>` | Unsubscribe from a channel |
| `/list_channels` | Show all subscribed channels |
| `/summary` | Generate a digest of the last 24 hours |
| `/help` | Show available commands |

### Example workflow

```
/add_channel durov
/add_channel telegram
/add_channel bbcnews
/summary
```

## Project Structure

```
telegram-aggregator-bot/
├── bot/
│   ├── __init__.py
│   ├── __main__.py          # Entry point — starts aiogram + Telethon
│   ├── config.py             # Environment-based configuration
│   ├── database.py           # SQLite storage (channels & messages)
│   ├── channel_reader.py     # Telethon MTProto client — reads channels
│   ├── summarizer.py         # Claude API — topic clustering & summarisation
│   ├── fact_checker.py       # Tavily API — claim verification
│   ├── formatter.py          # Report formatting for Telegram HTML
│   └── handlers.py           # aiogram command handlers
├── .env.example              # Template for environment variables
├── .gitignore
├── requirements.txt
├── PRD.md                    # Product requirements document
└── README.md                 # This file
```

## Stopping the Bot

Press `Ctrl+C` in the terminal. The bot will shut down gracefully.

## Troubleshooting

### "Missing required environment variable"

Make sure your `.env` file exists and all required variables are set. Compare with `.env.example`.

### Telethon session errors

Delete `user_session.session` and restart the bot to re-authenticate:

```bash
rm user_session.session
python -m bot
```

### Bot doesn't respond to commands

- Verify `OWNER_ID` matches your actual Telegram user ID.
- Make sure you started a conversation with the bot first (send `/start`).

### "Failed to fetch messages from @channel"

- The channel must be **public**.
- Your Telegram account (not the bot) must be able to access the channel.
- Check that the channel username is correct (without the `@` prefix).

### Rate limits

- Claude API and Tavily both have rate limits on free tiers.
- If summarisation fails, check the logs for API error details.
