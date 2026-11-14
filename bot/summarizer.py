from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import anthropic

logger = logging.getLogger(__name__)

# Server-side refusal fallback: if Sonnet 5.5's safety classifiers decline a
# request, the API retries it on Anthropic's recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
You are a news analyst. You turn a day's worth of Telegram channel posts into a \
structured digest for one busy reader.

Each input post starts with a reference like [ref=channel/123], followed by its \
UTC timestamp, the channel and the text. Media is shown as [photo], [video: title] etc.

Your job:
1. Cluster the posts into distinct topics: posts about the same event or story belong together.
2. Write a concise summary per topic (2-4 sentences) that merges what all the sources say.
3. Give each topic one category: Technology, Politics, Finance, Science, Sports, \
Entertainment, World, or Other.
4. List the refs of the posts each topic is based on (exactly as written in the input).
5. Cross-link related topics by their topic ids.
6. Extract 0-3 concrete, independently verifiable factual claims per topic \
(numbers, dates, named events, attributed statements). Skip opinions, predictions and vague statements.

Order topics by importance to a general reader, most important first; number ids from 1. \
Skip pure ads, giveaways and channel housekeeping posts. \
If nothing is worth summarising, return an empty topics list."""

TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "summary": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "related_topics": {"type": "array", "items": {"type": "integer"}},
                    "claims": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "id", "title", "category", "summary",
                    "sources", "related_topics", "claims",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topics"],
    "additionalProperties": False,
}


@dataclass
class FactCheck:
    claim: str
    verdict: str  # "verified" | "disputed" | "unverified"
    note: str = ""
    source_url: str = ""


@dataclass
class Topic:
    id: int
    title: str
    category: str
    summary: str
    sources: list[tuple[str, int]]  # (channel_username, telegram_msg_id)
    related_topics: list[int]
    claims: list[str]
    fact_checks: list[FactCheck] = field(default_factory=list)

    @property
    def channels(self) -> list[str]:
        return list(dict.fromkeys(ch for ch, _ in self.sources))


class SummarizationError(RuntimeError):
    pass


def _ref(channel: str, msg_id: int) -> str:
    return f"{channel}/{msg_id}"


class Summarizer:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model

    def _build_user_prompt(self, messages: list[dict]) -> str:
        lines: list[str] = []
        for msg in messages:
            channel = msg["channel_username"]
            parts = [
                f"[ref={_ref(channel, msg['telegram_msg_id'])}]",
                f"[{msg['date']}] @{channel}:",
            ]
            if msg.get("text"):
                parts.append(msg["text"])
            if msg.get("media_type"):
                caption = msg.get("media_caption")
                parts.append(
                    f"[{msg['media_type']}: {caption}]" if caption else f"[{msg['media_type']}]"
                )
            lines.append(" ".join(parts))
        return "\n\n".join(lines)

    async def summarize(self, messages: list[dict]) -> list[Topic]:
        if not messages:
            return []

        user_prompt = self._build_user_prompt(messages)
        logger.info(
            "Sending %d messages (%d chars) to %s for summarisation",
            len(messages), len(user_prompt), self._model,
        )

        async with self._client.beta.messages.stream(
            model=self._model,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": TOPICS_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            response = await stream.get_final_message()

        logger.info(
            "Summarisation done: model=%s stop=%s in=%d out=%d",
            response.model, response.stop_reason,
            response.usage.input_tokens, response.usage.output_tokens,
        )

        if response.stop_reason == "refusal":
            raise SummarizationError(f"Claude declined the request: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise SummarizationError("Claude's output hit max_tokens before finishing")

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SummarizationError("Claude returned invalid JSON") from exc

        known_refs = {
            _ref(m["channel_username"], m["telegram_msg_id"]) for m in messages
        }

        topics: list[Topic] = []
        for t in data["topics"]:
            sources: list[tuple[str, int]] = []
            for ref in t["sources"]:
                if ref not in known_refs:
                    logger.debug("Dropping unknown source ref %r", ref)
                    continue
                channel, msg_id = ref.rsplit("/", 1)
                sources.append((channel, int(msg_id)))
            topics.append(
                Topic(
                    id=t["id"],
                    title=t["title"],
                    category=t["category"],
                    summary=t["summary"],
                    sources=sources,
                    related_topics=t["related_topics"],
                    claims=t["claims"],
                )
            )

        logger.info("Extracted %d topics", len(topics))
        return topics
