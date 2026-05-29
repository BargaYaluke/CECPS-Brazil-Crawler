"""验证 browser_client.fetch_pncp_cookies 的关键行为。

不真启动 Chromium(那是端到端集成测试的事)。
这里 monkeypatch ``playwright.async_api.async_playwright`` 返回 fake 对象。
"""
from __future__ import annotations

from typing import Any

import pytest

from src.core.browser_client import fetch_pncp_cookies
from src.core.exceptions import BrowserError


# ─── 假 Playwright 对象树 ──────────────────────────────────────────────


class _FakeBrowser:
    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self._cookies = cookies
        self.closed = False

    async def new_context(self, user_agent: str | None = None) -> "_FakeContext":
        return _FakeContext(self._cookies)

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self._cookies = cookies

    async def new_page(self) -> "_FakePage":
        return _FakePage()

    async def cookies(self) -> list[dict[str, Any]]:
        return list(self._cookies)


class _FakePage:
    def set_default_navigation_timeout(self, ms: int) -> None:
        pass

    async def goto(self, url: str, wait_until: str = "load") -> None:
        pass

    async def wait_for_timeout(self, ms: int) -> None:
        pass


class _FakeChromium:
    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self._cookies = cookies

    async def launch(self, headless: bool = True) -> _FakeBrowser:
        return _FakeBrowser(self._cookies)


class _FakePW:
    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self.chromium = _FakeChromium(cookies)


class _FakePWContextManager:
    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self._cookies = cookies

    async def __aenter__(self) -> _FakePW:
        return _FakePW(self._cookies)

    async def __aexit__(self, *a: Any) -> None:
        return None


def _fake_async_playwright_factory(cookies: list[dict[str, Any]]):
    def _factory() -> _FakePWContextManager:
        return _FakePWContextManager(cookies)

    return _factory


# ─── 测试 ──────────────────────────────────────────────────────────────


async def test_fetch_filters_by_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """只保留以 TS 开头的 cookies(F5 ASM cookies)。"""
    fake_cookies = [
        {"name": "TS01234567", "value": "abc"},
        {"name": "TSPD_101_DID", "value": "def"},
        {"name": "JSESSIONID", "value": "xyz"},
        {"name": "_ga", "value": "tracker"},
    ]
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        _fake_async_playwright_factory(fake_cookies),
    )

    result = await fetch_pncp_cookies()

    assert result == {"TS01234567": "abc", "TSPD_101_DID": "def"}


async def test_fetch_raises_when_no_matching_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    """挑战没解过(没拿到 TS cookie)→ 抛 BrowserError。"""
    fake_cookies = [
        {"name": "JSESSIONID", "value": "xyz"},
        {"name": "_ga", "value": "tracker"},
    ]
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        _fake_async_playwright_factory(fake_cookies),
    )

    with pytest.raises(BrowserError, match="no cookies matching prefixes"):
        await fetch_pncp_cookies()


async def test_fetch_returns_all_when_no_prefix_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cookie_name_prefixes=[]`` 时不过滤,全量返回。"""
    fake_cookies = [
        {"name": "TSx", "value": "a"},
        {"name": "JSESSIONID", "value": "b"},
    ]
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        _fake_async_playwright_factory(fake_cookies),
    )

    result = await fetch_pncp_cookies(cookie_name_prefixes=[])

    assert result == {"TSx": "a", "JSESSIONID": "b"}


async def test_fetch_wraps_playwright_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Playwright 内部抛异常 → 包成 BrowserError。"""

    class _BrokenPW:
        async def __aenter__(self) -> None:
            raise RuntimeError("chromium binary not found")

        async def __aexit__(self, *a: Any) -> None:
            return None

    def _broken_factory() -> _BrokenPW:
        return _BrokenPW()

    monkeypatch.setattr("playwright.async_api.async_playwright", _broken_factory)

    with pytest.raises(BrowserError, match="playwright failed"):
        await fetch_pncp_cookies()
