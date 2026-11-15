from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

import anthropic

logger = logging.getLogger(__name__)

# Server-side refusal fallback: if Sonnet 5.5's safety classifiers decline a
# request, the API retries it on Anthropic's recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

DEFAULT_CATEGORIES = (
    "Technology", "Politics", "Finance", "Science",
    "Sports", "Entertainment", "World", "Other",
)

# Max stories in a weekly roundup, and fact-checks carried over per story
MAX_WEEKLY_STORIES = 12
MAX_WEEKLY_CHECKS = 4

SYSTEM_PROMPT = """\
You are a news analyst. You turn a day's worth of Telegram channel posts into a \
structured digest for one busy reader.

Each input post starts with a reference like [ref=channel/123], followed by its \
UTC timestamp, the channel and the text. Media is shown as [photo], [video: title] etc.

Your job:
1. Cluster the posts into distinct topics: posts about the same event or story belong together.
2. Write a concise summary per topic (2-4 sentences) that merges what all the sources say.
3. Give each topic one category: {categories}.
4. List the refs of the posts each topic is based on (exactly as written in the input).
5. Cross-link related topics by their topic ids.
6. Extract 0-3 concrete, independently verifiable factual claims per topic \
(numbers, dates, named events, attributed statements). Skip opinions, predictions and vague statements.

Order topics by importance to the reader, most important first; number ids from 1. \
Skip pure ads, giveaways and channel housekeeping posts. \
If nothing is worth summarising, return an empty topics list."""

WEEKLY_PROMPT = """\
You are a news editor. You turn a week of daily news digests into a weekly roundup \
for one busy reader.

Each input topic starts with a reference like [ref=3/2] (digest 3, topic 2), followed by \
its date, category, title, summary and fact-check results.

Your job:
1. Merge topics that cover the same story across days into one story.
2. Write a 2-4 sentence summary per story covering how it developed over the week. \
Do not present claims that fact-checks marked disputed as fact.
3. Give each story one category: {categories}.
4. List the refs of the daily topics each story is based on (exactly as written in the input).
5. Write an overview: one or two short sentences on the week's main themes.

Keep at most {max_stories} stories, most important first; drop minor one-off items."""

FOCUS_PROMPT = """

Reader focus: {focus}
Only include topics relevant to this focus; skip everything else, even if it is big news."""


def _topics_schema(categories: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "category": {"type": "string", "enum": list(categories)},
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


def _weekly_schema(categories: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": {
            "overview": {"type": "string"},
            "stories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "category": {"type": "string", "enum": list(categories)},
                        "summary": {"type": "string"},
                        "refs": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "category", "summary", "refs"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["overview", "stories"],
        "additionalProperties": False,
    }


@dataclass
class FactCheck:
    claim: str
    verdict: str  # "verified" | "partly_true" | "disputed" | "unverified"
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


def topic_to_dict(topic: Topic) -> dict:
    return asdict(topic)


def topic_from_dict(data: dict) -> Topic:
    return Topic(
        **{
            **data,
            "sources": [tuple(s) for s in data["sources"]],
            "fact_checks": [FactCheck(**fc) for fc in data["fact_checks"]],
        }
    )


def _ref(channel: str, msg_id: int) -> str:
    return f"{channel}/{msg_id}"


class Summarizer:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        model: str,
        focus: str = "",
        categories: tuple[str, ...] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._categories = tuple(categories or DEFAULT_CATEGORIES)
        if "Other" not in self._categories:
            self._categories += ("Other",)
        self._focus = FOCUS_PROMPT.format(focus=focus) if focus else ""

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

    async def _request(self, system: str, prompt: str, schema: dict) -> dict:
        """One structured-output call; returns the parsed JSON object."""
        async with self._client.beta.messages.stream(
            model=self._model,
            max_tokens=64000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": schema},
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
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SummarizationError("Claude returned invalid JSON") from exc

    async def summarize(self, messages: list[dict]) -> list[Topic]:
        if not messages:
            return []

        user_prompt = self._build_user_prompt(messages)
        logger.info(
            "Sending %d messages (%d chars) to %s for summarisation",
            len(messages), len(user_prompt), self._model,
        )
        system = SYSTEM_PROMPT.format(categories=", ".join(self._categories)) + self._focus
        data = await self._request(system, user_prompt, _topics_schema(self._categories))

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

    async def summarize_week(
        self, days: list[tuple[str, list[Topic]]],
    ) -> tuple[str, list[Topic]]:
        """Merge a week of daily digests into (overview, stories).

        `days` is [(date label, topics)]. Stories inherit the sources and
        fact-checks of the daily topics they are built from.
        """
        by_ref: dict[str, Topic] = {}
        lines: list[str] = []
        for d, (label, topics) in enumerate(days, 1):
            for t in topics:
                ref = f"{d}/{t.id}"
                by_ref[ref] = t
                checks = "; ".join(f"{fc.verdict}: {fc.claim}" for fc in t.fact_checks)
                line = f"[ref={ref}] ({label}) [{t.category}] {t.title}: {t.summary}"
                if checks:
                    line += f" Fact-checks: {checks}"
                lines.append(line)
        if not lines:
            return "", []

        logger.info("Building weekly roundup from %d daily topics", len(lines))
        system = WEEKLY_PROMPT.format(
            categories=", ".join(self._categories), max_stories=MAX_WEEKLY_STORIES,
        ) + self._focus
        data = await self._request(system, "\n\n".join(lines), _weekly_schema(self._categories))

        stories: list[Topic] = []
        for s in data["stories"][:MAX_WEEKLY_STORIES]:
            merged = [by_ref[r] for r in s["refs"] if r in by_ref]
            checks = {fc.claim: fc for t in merged for fc in t.fact_checks}
            stories.append(
                Topic(
                    id=len(stories) + 1,
                    title=s["title"],
                    category=s["category"],
                    summary=s["summary"],
                    sources=list(dict.fromkeys(src for t in merged for src in t.sources)),
                    related_topics=[],
                    claims=[],
                    fact_checks=list(checks.values())[:MAX_WEEKLY_CHECKS],
                )
            )
        logger.info("Weekly roundup: %d stories", len(stories))
        return data["overview"], stories
