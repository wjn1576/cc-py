"""Tests for WebSearchTool."""

import pytest

from cc.tools.web_search import web_search_tool
from cc.tools.web_search.web_search_tool import WebSearchTool


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_search_requires_bocha_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BOCHA_API_KEY", raising=False)
        monkeypatch.delenv("BOCHAAI_API_KEY", raising=False)
        tool = WebSearchTool()
        result = await tool.execute({"query": "python testing"})
        assert result.is_error
        assert "BOCHA_API_KEY" in result.content

    @pytest.mark.asyncio
    async def test_search_formats_bocha_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeResponse:
            status_code = 200
            text = ""

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "DeepSeek",
                                    "url": "https://example.com/deepseek",
                                    "siteName": "Example",
                                    "summary": "DeepSeek timeline summary.",
                                    "datePublished": "2026-01-01",
                                }
                            ]
                        }
                    }
                }

        class _FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "_FakeClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
                return _FakeResponse()

        monkeypatch.setattr(web_search_tool.httpx, "AsyncClient", _FakeClient)
        tool = WebSearchTool(api_key="bocha-test-key")
        result = await tool.execute({"query": "DeepSeek 发展历程", "max_results": 3})

        assert not result.is_error
        assert "Search results for: DeepSeek 发展历程" in result.content
        assert "https://example.com/deepseek" in result.content
        assert "DeepSeek timeline summary." in result.content

    @pytest.mark.asyncio
    async def test_empty_query_error(self) -> None:
        tool = WebSearchTool()
        result = await tool.execute({"query": ""})
        assert result.is_error
        assert "required" in result.content

    @pytest.mark.asyncio
    async def test_schema(self) -> None:
        tool = WebSearchTool()
        assert tool.get_name() == "WebSearch"
        schema = tool.get_schema()
        assert schema.name == "WebSearch"
        assert "query" in schema.input_schema["properties"]
        assert "max_results" in schema.input_schema["properties"]
        assert "freshness" in schema.input_schema["properties"]
        assert "query" in schema.input_schema["required"]

    @pytest.mark.asyncio
    async def test_concurrency_safe(self) -> None:
        tool = WebSearchTool()
        assert tool.is_concurrency_safe({}) is True
