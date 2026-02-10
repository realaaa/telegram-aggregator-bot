from __future__ import annotations

import logging

from tavily import TavilyClient

from bot.summarizer import Topic

logger = logging.getLogger(__name__)

VERDICT_VERIFIED = "\u2713 Verified"
VERDICT_DISPUTED = "\u26a0 Disputed"
VERDICT_UNVERIFIED = "? Unverified"


class FactChecker:
    def __init__(self, api_key: str) -> None:
        self._client = TavilyClient(api_key=api_key)

    def _assess_results(self, claim: str, results: list[dict]) -> str:
        """Simple heuristic: if we get relevant results, mark verified; otherwise unverified."""
        if not results:
            return VERDICT_UNVERIFIED

        # Check if any result's content mentions keywords that suggest the claim is disputed
        dispute_keywords = [
            "false", "misleading", "debunked", "hoax", "incorrect",
            "not true", "fabricated", "misinformation", "disinformation",
        ]
        support_keywords = [
            "confirmed", "verified", "true", "correct", "according to",
            "official", "announced", "reported",
        ]

        dispute_score = 0
        support_score = 0

        for r in results:
            content = (r.get("content") or "").lower()
            for kw in dispute_keywords:
                if kw in content:
                    dispute_score += 1
            for kw in support_keywords:
                if kw in content:
                    support_score += 1

        if dispute_score > support_score and dispute_score >= 2:
            return VERDICT_DISPUTED
        if support_score > 0:
            return VERDICT_VERIFIED
        return VERDICT_UNVERIFIED

    async def check_claims(self, topics: list[Topic]) -> list[Topic]:
        """Fact-check claims in each topic and annotate them with verdicts."""
        for topic in topics:
            if not topic.claims:
                topic.fact_checks = {}
                continue

            verdicts: dict[str, str] = {}
            for claim in topic.claims:
                try:
                    response = self._client.search(
                        query=claim,
                        search_depth="basic",
                        max_results=5,
                    )
                    results = response.get("results", [])
                    verdicts[claim] = self._assess_results(claim, results)
                except Exception:
                    logger.exception("Tavily search failed for claim: %s", claim)
                    verdicts[claim] = VERDICT_UNVERIFIED

            topic.fact_checks = verdicts
            logger.info(
                "Topic '%s': %d claims checked", topic.title, len(verdicts)
            )

        return topics
