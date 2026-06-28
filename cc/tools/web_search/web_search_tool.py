"""WebSearchTool — Bocha web search integration.

Corresponds to TS: tools/WebSearchTool.

该工具调用博查 Web Search API，为模型提供联网搜索能力。
配置方式：设置 BOCHA_API_KEY 环境变量，或在当前工作目录 .env 中配置。
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

WEB_SEARCH_TOOL_NAME = "WebSearch"
BOCHA_WEB_SEARCH_ENDPOINT = "https://api.bochaai.com/v1/web-search"


class WebSearchTool(Tool):
    """Search the web for information.

    Corresponds to TS: tools/WebSearchTool.
    Uses Bocha Web Search API and returns a compact text summary.
    """

    def __init__(self, api_key: str | None = None, endpoint: str = BOCHA_WEB_SEARCH_ENDPOINT) -> None:
        self._api_key = api_key
        self._endpoint = endpoint

    def get_name(self) -> str:
        return WEB_SEARCH_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=WEB_SEARCH_TOOL_NAME,
            description="Search the web for information using a search query.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 5,
                    },
                    "freshness": {
                        "type": "string",
                        "description": (
                            "Freshness filter: noLimit, oneDay, oneWeek, oneMonth, oneYear, "
                            "YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD"
                        ),
                        "default": "noLimit",
                    },
                    "summary": {
                        "type": "boolean",
                        "description": "Whether to request text summaries for search results",
                        "default": True,
                    },
                    "include": {
                        "type": "string",
                        "description": "Limit search to domains/subdomains, separated by | or comma",
                    },
                    "exclude": {
                        "type": "string",
                        "description": "Exclude domains/subdomains, separated by | or comma",
                    },
                },
                "required": ["query"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 搜索请求为只读外部 API 调用，不修改本地状态，可安全并发
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        query = tool_input.get("query", "")
        if not query:
            return ToolResult(content="Error: query is required", is_error=True)

        api_key = self._api_key or os.environ.get("BOCHA_API_KEY") or os.environ.get("BOCHAAI_API_KEY")
        if not api_key:
            return ToolResult(
                content=(
                    "WebSearch requires Bocha API key configuration. "
                    "Set BOCHA_API_KEY in your environment or current project .env file."
                ),
                is_error=True,
            )

        max_results = _coerce_count(tool_input.get("max_results", 5))
        freshness = str(tool_input.get("freshness") or "noLimit")
        payload = {
            "query": query,
            "summary": bool(tool_input.get("summary", True)),
            "freshness": freshness,
            "count": max_results,
        }
        include = str(tool_input.get("include") or "").strip()
        exclude = str(tool_input.get("exclude") or "").strip()
        if include:
            payload["include"] = include
        if exclude:
            payload["exclude"] = exclude

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300] if e.response is not None else ""
            return ToolResult(
                content=f"Bocha WebSearch HTTP {e.response.status_code}: {body}",
                is_error=True,
            )
        except Exception as e:
            logger.debug("Bocha WebSearch failed: %s", e)
            return ToolResult(content=f"Bocha WebSearch failed: {e}", is_error=True)

        return ToolResult(
            content=_format_bocha_results(query=query, data=data, max_results=max_results),
            is_error=False,
        )


def _coerce_count(value: Any) -> int:
    """Convert max_results to Bocha count range."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 5
    return max(1, min(count, 20))


def _format_bocha_results(*, query: str, data: dict[str, Any], max_results: int) -> str:
    """Format Bocha response into a model-friendly plain text list."""
    payload = data.get("data", data)
    web_pages = payload.get("webPages", {}) if isinstance(payload, dict) else {}
    items = web_pages.get("value", []) if isinstance(web_pages, dict) else []
    if not isinstance(items, list) or not items:
        return f"No web search results found for: {query}"

    lines = [f"Search results for: {query}", ""]
    for idx, item in enumerate(items[:max_results], 1):
        if not isinstance(item, dict):
            continue

        title = item.get("name") or item.get("title") or "(untitled)"
        url = item.get("url") or item.get("displayUrl") or ""
        site_name = item.get("siteName") or item.get("site") or ""
        snippet = item.get("summary") or item.get("snippet") or item.get("description") or ""
        date_label, date_value = _extract_result_date(item)

        lines.append(f"{idx}. {title}")
        if url:
            lines.append(f"   URL: {url}")
        if site_name:
            lines.append(f"   Site: {site_name}")
        if date_value:
            lines.append(f"   {date_label}: {date_value}")
        if snippet:
            lines.append(f"   Summary: {snippet}")
        lines.append("")

    return "\n".join(lines).strip()


def _extract_result_date(item: dict[str, Any]) -> tuple[str, str]:
    """Prefer page publish time; fall back to Bocha crawl time with timezone note."""
    published = item.get("datePublished")
    if published:
        return "Published", str(published)

    crawled = item.get("dateLastCrawled")
    if not crawled:
        return "Date", ""

    crawled_text = str(crawled)
    normalized = _normalize_bocha_crawled_time(crawled_text)
    if normalized != crawled_text:
        return "Last crawled", f"{normalized} (UTC+8, normalized from Bocha dateLastCrawled)"
    return "Last crawled", crawled_text


def _normalize_bocha_crawled_time(value: str) -> str:
    """Normalize Bocha dateLastCrawled values that use Z for UTC+8 wall time.

    Bocha documents note that dateLastCrawled may be returned like
    2025-02-23T08:18:30Z while semantically meaning UTC+8 local time.
    When that pattern appears, present it explicitly as +08:00.
    """
    if not value.endswith("Z"):
        return value

    try:
        naive = datetime.fromisoformat(value[:-1])
    except ValueError:
        return value

    beijing = timezone(timedelta(hours=8))
    local_time = naive.replace(tzinfo=beijing)
    utc_time = local_time.astimezone(UTC)
    return f"{local_time.isoformat(timespec='seconds')} / {utc_time.isoformat(timespec='seconds')}"
