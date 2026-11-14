"""Publish digests as Telegra.ph pages (https://telegra.ph/api)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp

from bot.formatter import VERDICT_LABELS, group_sources, msg_link
from bot.summarizer import Topic

logger = logging.getLogger(__name__)

API_URL = "https://api.telegra.ph"
AUTHOR_NAME = "News Aggregator Bot"
# Telegraph rejects page content above 64 KB
MAX_CONTENT_BYTES = 64 * 1024

Node = str | dict[str, Any]


class TelegraphError(RuntimeError):
    pass


def _el(tag: str, *children: Node, **attrs: str) -> dict[str, Any]:
    node: dict[str, Any] = {"tag": tag}
    if attrs:
        node["attrs"] = attrs
    if children:
        node["children"] = list(children)
    return node


def _a(href: str, text: str) -> dict[str, Any]:
    return _el("a", text, href=href)


def build_digest_nodes(
    topics: list[Topic], hours: int, failed_channels: list[str] | None = None,
) -> list[Node]:
    """Convert topics into Telegraph content nodes."""
    titles = {t.id: t.title for t in topics}
    n_channels = len({ch for t in topics for ch in t.channels})
    nodes: list[Node] = [
        _el("p", _el("i", f"{len(topics)} topics from {n_channels} channels, last {hours} hours.")),
        _el("h4", "Contents"),
        _el("ol", *(_el("li", f"{t.title} [{t.category}]") for t in topics)),
    ]

    for t in topics:
        nodes.append(_el("hr"))
        nodes.append(_el("h3", f"{t.id}. {t.title}"))
        nodes.append(_el("p", _el("i", t.category)))
        nodes.append(_el("p", t.summary))

        if t.fact_checks:
            items = []
            for fc in t.fact_checks:
                children: list[Node] = [
                    _el("b", VERDICT_LABELS.get(fc.verdict, fc.verdict)), f" — {fc.claim}",
                ]
                if fc.note:
                    children += [_el("br"), _el("i", fc.note)]
                if fc.source_url.startswith(("http://", "https://")):
                    children += [" ", _a(fc.source_url, "(source)")]
                items.append(_el("li", *children))
            nodes.append(_el("h4", "Fact-check"))
            nodes.append(_el("ul", *items))

        sources: list[Node] = ["Sources: "]
        for i, (channel, ids) in enumerate(group_sources(t).items()):
            if i:
                sources.append(", ")
            if len(ids) == 1:
                sources.append(_a(msg_link(channel, ids[0]), f"@{channel}"))
            else:
                sources.append(_a(msg_link(channel), f"@{channel}"))
                for n, msg_id in enumerate(ids, 1):
                    sources += [" ", _a(msg_link(channel, msg_id), f"[{n}]")]
        if len(sources) > 1:
            nodes.append(_el("p", *sources))

        related = [titles[r] for r in t.related_topics if r in titles and r != t.id]
        if related:
            nodes.append(_el("p", _el("i", "Related: " + "; ".join(related))))

    if failed_channels:
        nodes.append(_el("hr"))
        nodes.append(
            _el("p", "⚠️ Could not read: " + ", ".join(f"@{c}" for c in failed_channels))
        )
    return nodes


class TelegraphClient:
    def __init__(self, data_dir: Path) -> None:
        self._token_path = data_dir / "telegraph.json"
        self._token: str | None = None
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def _call(self, method: str, **params: Any) -> dict[str, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        async with self._session.post(f"{API_URL}/{method}", data=params) as resp:
            data = await resp.json(content_type=None)
        if not data.get("ok"):
            raise TelegraphError(f"Telegraph {method} failed: {data.get('error')}")
        return data["result"]

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        if self._token_path.exists():
            self._token = json.loads(self._token_path.read_text())["access_token"]
            return self._token

        account = await self._call(
            "createAccount", short_name="newsaggrobot", author_name=AUTHOR_NAME,
        )
        self._token = account["access_token"]
        self._token_path.write_text(json.dumps({"access_token": self._token}))
        os.chmod(self._token_path, 0o600)
        logger.info("Created Telegraph account, token saved to %s", self._token_path)
        return self._token

    async def create_page(self, title: str, nodes: list[Node]) -> str:
        """Publish a page and return its URL."""
        content = json.dumps(nodes, ensure_ascii=False)
        size = len(content.encode())
        if size > MAX_CONTENT_BYTES:
            raise TelegraphError(f"Digest too large for Telegraph ({size} bytes)")

        page = await self._call(
            "createPage",
            access_token=await self._get_token(),
            title=title[:256],
            author_name=AUTHOR_NAME,
            content=content,
            return_content="false",
        )
        logger.info("Published Telegraph page %s (%d bytes)", page["url"], size)
        return page["url"]
