from __future__ import annotations

from bot.summarizer import Topic

TELEGRAM_MSG_LIMIT = 4096


def _msg_link(channel: str, msg_id: int | None = None) -> str:
    """Build a t.me link for a channel (or specific message)."""
    base = f"https://t.me/{channel}"
    if msg_id:
        return f"{base}/{msg_id}"
    return base


def format_report(topics: list[Topic]) -> list[str]:
    """Format topics into one or more Telegram HTML messages."""
    if not topics:
        return ["No updates found in the last 24 hours."]

    sections: list[str] = []

    for topic in topics:
        lines: list[str] = []

        # Header: title + category
        lines.append(
            f"<b>{topic.id}. {_escape(topic.title)}</b>  "
            f"<i>[{_escape(topic.category)}]</i>"
        )

        # Summary
        lines.append(_escape(topic.summary))

        # Fact-checks
        if topic.fact_checks:
            fc_lines = []
            for claim, verdict in topic.fact_checks.items():
                fc_lines.append(f"  {verdict} — {_escape(claim)}")
            if fc_lines:
                lines.append("<b>Fact-check:</b>")
                lines.extend(fc_lines)

        # Source channels
        channel_links = [
            f'<a href="{_msg_link(ch)}">@{_escape(ch)}</a>'
            for ch in topic.channels
        ]
        if channel_links:
            lines.append(f"Sources: {', '.join(channel_links)}")

        # Related topics
        if topic.related_topics:
            refs = [f"#{tid}" for tid in topic.related_topics]
            lines.append(f"Related: {', '.join(refs)}")

        sections.append("\n".join(lines))

    # Join sections and split into messages if needed
    full_text = "\n\n---\n\n".join(sections)
    header = "<b>Daily Digest</b>\n\n"

    if len(header + full_text) <= TELEGRAM_MSG_LIMIT:
        return [header + full_text]

    # Split into multiple messages
    messages: list[str] = []
    current = header
    for section in sections:
        separator = "\n\n---\n\n"
        candidate = current + separator + section if current != header else current + section
        if len(candidate) > TELEGRAM_MSG_LIMIT:
            if current != header:
                messages.append(current)
            current = section
        else:
            current = candidate

    if current:
        messages.append(current)

    return messages


def _escape(text: str) -> str:
    """Escape HTML special chars for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
