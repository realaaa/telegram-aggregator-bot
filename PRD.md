# Product Requirements Document — Telegram Aggregator Bot

## 1. Project Overview

**Goal:** Build a personal Telegram bot that delivers a condensed, topic-clustered summary of updates from public Telegram channels — fact-checked via web search — so the user can stay informed without reading every channel individually.

**Target user:** Single user (personal use).

**Value proposition:** Save time by automatically collecting, clustering, summarising, and fact-checking messages from dozens of public Telegram channels into one on-demand report.

---

## 2. Functional Requirements

### 2.1 Channel Management

- User adds/removes public Telegram channels via bot commands.
- Channel list is persisted in a local SQLite database.
- Channels are stored by their public username (e.g. `@durov`).

### 2.2 Message Collection

- A Telethon MTProto user client runs in the background and reads messages from all subscribed public channels.
- Messages from the last 24 hours (fixed window) are collected when a summary is requested.
- Collected messages (text, metadata, media references) are stored in SQLite for processing.

### 2.3 Summarisation Pipeline

- Uses Claude API (Anthropic) to:
  1. **Cluster** messages into topics across channels.
  2. **Summarise** each topic cluster into a concise digest.
  3. **Cross-link** related topics and highlight where multiple channels cover the same story.
  4. **Categorise** topics (e.g. tech, politics, finance, etc.).

### 2.4 Fact-Checking

- Key claims identified during summarisation are verified via Tavily API web search.
- Fact-check results are appended inline to the relevant topic summary (e.g. "✓ Verified" / "⚠ Disputed" / "? Unverified").

### 2.5 Media Handling

- Images, videos, and documents shared in channels are acknowledged in the summary.
- Where possible, media captions and context are included in the summarisation pipeline.
- Binary media files are not stored long-term; only metadata and captions are retained.

### 2.6 Report Delivery

- The final report is sent as a formatted Telegram message (Markdown/HTML) via aiogram.
- Structure: topic-based clusters, each containing:
  - Topic title and category tag
  - Concise summary
  - Fact-check annotations
  - Links to original messages
- Long reports are split across multiple messages if they exceed Telegram's message length limit (4096 chars).

---

## 3. Architecture

### 3.1 High-Level Design

Hybrid architecture combining two Telegram client libraries:

| Layer | Library | Protocol | Role |
|---|---|---|---|
| User interaction | aiogram | Bot API | Handle commands, deliver reports |
| Channel reading | Telethon | MTProto (user client) | Read public channel messages |

### 3.2 Component Diagram

```
┌──────────────────────────────────────────────────┐
│                  Telegram User                   │
│               (bot commands/reports)             │
└──────────────────┬───────────────────────────────┘
                   │  Bot API
         ┌─────────▼─────────┐
         │   Bot Interface   │  ← aiogram
         │  (commands, UI)   │
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │  Channel Reader   │  ← Telethon (MTProto)
         │ (fetch messages)  │
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │  Message Store    │  ← SQLite
         │ (channels, msgs)  │
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │  Summarisation    │  ← Claude API
         │     Engine        │
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │   Fact Checker    │  ← Tavily API
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │ Report Formatter  │
         │ (Markdown/HTML)   │
         └───────────────────┘
```

### 3.3 Data Flow

1. User sends `/summary` to the bot.
2. Bot Interface triggers Channel Reader.
3. Channel Reader fetches messages from the last 24 hours across all subscribed channels via Telethon.
4. Messages are stored/cached in Message Store (SQLite).
5. Summarisation Engine (Claude API) clusters, summarises, cross-links, and categorises the messages.
6. Fact Checker (Tavily API) verifies key claims extracted during summarisation.
7. Report Formatter assembles the final Telegram message(s).
8. Bot Interface delivers the report to the user.

---

## 4. Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Bot framework | aiogram 3.x |
| Channel reader | Telethon |
| AI / summarisation | Anthropic SDK (Claude API) |
| Fact-checking | Tavily API |
| Database | SQLite (via aiosqlite) |
| Config | Environment variables (.env) |

---

## 5. Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and usage instructions |
| `/summary` | Generate and deliver a summary of the last 24 hours |
| `/add_channel <username>` | Subscribe to a public channel |
| `/remove_channel <username>` | Unsubscribe from a channel |
| `/list_channels` | Show all subscribed channels |
| `/help` | Show available commands and usage guide |

---

## 6. Non-Functional Requirements

- **Single-user deployment** — no auth/multi-tenancy needed.
- **Local execution** — runs on the user's machine; no cloud infra required.
- **Environment-based config** — all secrets (API keys, bot token, Telethon session) via `.env` file.
- **Resilience** — graceful error handling when APIs are unavailable; partial summaries over no summary.
- **Logging** — structured logging for debugging and monitoring.

---

## 7. Out of Scope (v1)

- Multi-user support / authentication
- Scheduled / automatic digests
- Keyword-based alerts
- Priority ranking of topics
- Real-time push notifications
- Private channel or group reading
- Web UI / dashboard
