from __future__ import annotations

import logging

import anthropic

from bot.summarizer import FALLBACK_BETA, FactCheck, Topic

logger = logging.getLogger(__name__)

VERDICTS = ("verified", "partly_true", "disputed", "unverified")

# Hard cap on web searches per digest (billed per search)
MAX_SEARCHES = 25
# Max server-side continuations (pause_turn) + reminder turns before giving up
MAX_TURNS = 6

SYSTEM_PROMPT = """\
You are a fact-checker. You receive numbered factual claims taken from today's \
Telegram news channels. Check each claim against current web sources with the \
web_search tool; do not rely on your training knowledge, since these are recent events. \
Group related claims into shared searches where you can.

Verdicts:
- verified: reputable independent sources confirm the claim, including its specific details \
(numbers, dates, names, qualifiers).
- partly_true: the core of the claim holds, but a detail is wrong, overstated, missing a \
qualifier, or could not be confirmed.
- disputed: reputable sources contradict the core of the claim, or it has been debunked or corrected.
- unverified: you could not find reliable confirmation either way.

Use verified only when every detail checks out; if your note would mention a caveat, \
the verdict is partly_true.

When you are done, call report_fact_checks exactly once with a verdict for every claim id. \
For each, add a one-sentence note on what the sources say and the URL of the best source \
(empty string if none). If you did not search for a claim, say so in its note and mark it unverified."""

REPORT_TOOL = {
    "name": "report_fact_checks",
    "description": "Submit the final verdict for every claim. Call once, after searching.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "integer"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "note": {"type": "string"},
                        "source_url": {"type": "string"},
                    },
                    "required": ["claim_id", "verdict", "note", "source_url"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["results"],
        "additionalProperties": False,
    },
}


class FactChecker:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model

    async def check_claims(self, topics: list[Topic]) -> list[Topic]:
        """Fact-check all claims across topics in one Claude + web search session."""
        claims: list[tuple[Topic, str]] = [
            (topic, claim) for topic in topics for claim in topic.claims
        ]
        if not claims:
            return topics

        listing = "\n".join(
            f"{i}. [{topic.title}] {claim}" for i, (topic, claim) in enumerate(claims, 1)
        )
        messages: list[dict] = [{"role": "user", "content": f"Claims to check:\n\n{listing}"}]
        tools = [
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": min(MAX_SEARCHES, 2 * len(claims)),
            },
            REPORT_TOOL,
        ]

        results: list[dict] | None = None
        for _ in range(MAX_TURNS):
            response = await self._client.beta.messages.create(
                model=self._model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
                output_config={"effort": "medium"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            logger.info(
                "Fact-check turn: stop=%s in=%d out=%d",
                response.stop_reason,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )

            report = next(
                (
                    b for b in response.content
                    if b.type == "tool_use" and b.name == "report_fact_checks"
                ),
                None,
            )
            if report is not None:
                results = report.input["results"]
                break

            if response.stop_reason == "refusal":
                logger.warning("Fact-check declined: %s", response.stop_details)
                break

            # Keep the full assistant turn (incl. search results) in history
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "pause_turn":
                continue  # server-side search loop paused: resend to resume
            messages.append(
                {
                    "role": "user",
                    "content": "Please submit your verdicts now with report_fact_checks.",
                }
            )

        by_id = {r["claim_id"]: r for r in results or []}
        for topic in topics:
            topic.fact_checks = []
        for i, (topic, claim) in enumerate(claims, 1):
            r = by_id.get(i)
            topic.fact_checks.append(
                FactCheck(
                    claim=claim,
                    verdict=r["verdict"] if r else "unverified",
                    note=r["note"] if r else "",
                    source_url=r["source_url"] if r else "",
                )
            )

        logger.info("Fact-checked %d/%d claims", len(by_id), len(claims))
        return topics
