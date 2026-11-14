from __future__ import annotations

from datetime import datetime
from html import escape

from bot.summarizer import Topic

TELEGRAM_MSG_LIMIT = 4096
SECTION_SEPARATOR = "\n\n"
MAX_LINKS_PER_CHANNEL = 5
DATE_FORMAT = "%a %d %b %Y"

VERDICT_LABELS = {
    "verified": "✅ Verified",
    "disputed": "⚠️ Disputed",
    "unverified": "❔ Unverified",
}


def msg_link(channel: str, msg_id: int | None = None) -> str:
    """Build a t.me link for a channel (or specific message)."""
    base = f"https://t.me/{channel}"
    return f"{base}/{msg_id}" if msg_id else base


def group_sources(topic: Topic) -> dict[str, list[int]]:
    """Map channel -> sorted, de-duplicated message ids (capped per channel)."""
    by_channel: dict[str, set[int]] = {}
    for channel, msg_id in topic.sources:
        by_channel.setdefault(channel, set()).add(msg_id)
    return {ch: sorted(ids)[:MAX_LINKS_PER_CHANNEL] for ch, ids in by_channel.items()}


def _a(url: str, text: str) -> str:
    return f'<a href="{escape(url, quote=True)}">{escape(text)}</a>'


def _format_sources(topic: Topic) -> str | None:
    by_channel = group_sources(topic)
    if not by_channel:
        return None

    parts = []
    for channel, ids in by_channel.items():
        if len(ids) == 1:
            parts.append(_a(msg_link(channel, ids[0]), f"@{channel}"))
        else:
            posts = " ".join(_a(msg_link(channel, i), f"[{n}]") for n, i in enumerate(ids, 1))
            parts.append(f"{_a(msg_link(channel), f'@{channel}')} {posts}")
    return "Sources: " + ", ".join(parts)


def _format_topic(topic: Topic) -> str:
    lines = [
        f"<b>{topic.id}. {escape(topic.title)}</b>  <i>[{escape(topic.category)}]</i>",
        escape(topic.summary),
    ]

    if topic.fact_checks:
        lines.append("<b>Fact-check:</b>")
        for fc in topic.fact_checks:
            line = f"{VERDICT_LABELS.get(fc.verdict, fc.verdict)} — {escape(fc.claim)}"
            if fc.note:
                line += f"\n    <i>{escape(fc.note)}</i>"
            if fc.source_url.startswith(("http://", "https://")):
                line += f" {_a(fc.source_url, '(source)')}"
            lines.append(line)

    sources = _format_sources(topic)
    if sources:
        lines.append(sources)

    if topic.related_topics:
        lines.append("Related: " + ", ".join(f"#{tid}" for tid in topic.related_topics))

    return "\n".join(lines)


def _split_long_line(line: str, limit: int) -> list[str]:
    """Split a single over-long line at whitespace (last resort: hard cut)."""
    chunks: list[str] = []
    while len(line) > limit:
        cut = line.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(line[:cut])
        line = line[cut:].lstrip()
    if line:
        chunks.append(line)
    return chunks


def _split_section(section: str, limit: int) -> list[str]:
    """Split one section into <= limit chunks on line boundaries (keeps HTML tags intact)."""
    if len(section) <= limit:
        return [section]
    chunks: list[str] = []
    current = ""
    for line in section.split("\n"):
        for piece in _split_long_line(line, limit):
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) > limit:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def format_report(
    topics: list[Topic], hours: int, now: datetime | None = None,
) -> list[str]:
    """Format topics into one or more Telegram HTML messages (each <= 4096 chars)."""
    if not topics:
        return [f"No updates found in the last {hours} hours."]

    date_str = (now or datetime.now()).strftime(DATE_FORMAT)
    n = len(topics)
    header = (
        f"<b>📰 Daily Digest — {date_str}</b>\n"
        f"<i>{n} topic{'s' if n != 1 else ''}, last {hours}h</i>"
    )

    pieces = [header]
    for topic in topics:
        pieces.extend(_split_section(_format_topic(topic), TELEGRAM_MSG_LIMIT))

    messages: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{SECTION_SEPARATOR}{piece}" if current else piece
        if len(candidate) > TELEGRAM_MSG_LIMIT:
            messages.append(current)
            current = piece
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
