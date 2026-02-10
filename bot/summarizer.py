from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a news analyst that processes Telegram channel messages and produces a structured digest.

Given a list of messages from various Telegram channels, you must:
1. Cluster messages into distinct topics (group messages about the same event/story together).
2. For each topic, write a concise summary (2-4 sentences).
3. Assign a category tag to each topic (e.g. Technology, Politics, Finance, Science, Sports, Entertainment, World, Other).
4. Identify which channels covered each topic.
5. Cross-link related topics by referencing their topic numbers.
6. Extract 0-3 key factual claims per topic that should be fact-checked. Only extract concrete, verifiable claims — not opinions or vague statements.

Return ONLY valid JSON matching this schema (no markdown fences):
{
  "topics": [
    {
      "id": 1,
      "title": "Short topic title",
      "category": "Category",
      "summary": "Concise summary of the topic.",
      "channels": ["channel1", "channel2"],
      "related_topics": [2],
      "claims": ["Specific factual claim to verify"]
    }
  ]
}

If there are no messages or nothing meaningful to summarise, return: {"topics": []}
"""


@dataclass
class Topic:
    id: int
    title: str
    category: str
    summary: str
    channels: list[str]
    related_topics: list[int]
    claims: list[str]
    fact_checks: dict[str, str] | None = None  # claim -> verdict, filled later


class Summarizer:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5-20250929") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _build_user_prompt(self, messages: list[dict]) -> str:
        lines: list[str] = []
        for msg in messages:
            channel = msg["channel_username"]
            date = msg["date"]
            text = msg.get("text") or ""
            media = msg.get("media_type")
            caption = msg.get("media_caption")

            parts = [f"[{date}] @{channel}:"]
            if text:
                parts.append(text[:1000])  # truncate very long messages
            if media:
                detail = f"[{media}]"
                if caption:
                    detail = f"[{media}: {caption}]"
                parts.append(detail)
            lines.append(" ".join(parts))

        return "\n".join(lines)

    async def summarize(self, messages: list[dict]) -> list[Topic]:
        if not messages:
            return []

        user_prompt = self._build_user_prompt(messages)
        logger.info(
            "Sending %d messages (%d chars) to Claude for summarisation",
            len(messages),
            len(user_prompt),
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown fences if the model includes them
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Failed to parse Claude response as JSON: %s", raw[:500])
            return []

        topics: list[Topic] = []
        for t in data.get("topics", []):
            topics.append(
                Topic(
                    id=t["id"],
                    title=t["title"],
                    category=t.get("category", "Other"),
                    summary=t["summary"],
                    channels=t.get("channels", []),
                    related_topics=t.get("related_topics", []),
                    claims=t.get("claims", []),
                )
            )

        logger.info("Extracted %d topics", len(topics))
        return topics
