"""tests/unit/test_pncp_session.py — PncpSession 传输层单测。

不真启动 Chromium:browser 后端用 monkeypatch 替成 FakeBrowser;httpx 后端用
pytest_httpx mock。覆盖三种 transport + auto 撞 F5 自动 fallback。
"""
from __future__ import annotations

import re
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.core.exceptions import F5ChallengeError
from src.fetchers import pncp_session as ps_mod
from src.fetchers.pncp_session import PncpSession

PNCP_RE = re.compile(r"https://pncp\.gov\.br/.*")
URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"

# F5 BIG-IP ASM 挑战页特征(window["bobcmn"])
F5_HTML = (
    b"<!DOCTYPE html><html><head></head>"
    b'<script>window["bobcmn"]="1011111";</script></html>'
)


class FakeBrowser:
    """假 BrowserSession:实现 __aenter__/__aexit__/get_json,返回浏览器标记。"""

    last_instance: "FakeBrowser | None" = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[Any] = []
        FakeBrowser.last_instance = self

    async def __aenter__(self) -> "FakeBrowser":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get_json(self, url: str, params: dict | None = None) -> Any:
        self.calls.append((url, params))
        return {"via": "browser"}


async def test_httpx_mode_uses_httpx(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=PNCP_RE, json={"via": "httpx", "data": []})
    async with PncpSession(transport="httpx", rate_limit_per_second=0) as s:
        assert s.backend == "httpx"
        data = await s.get_json(URL, params={"pagina": 1})
    assert data == {"via": "httpx", "data": []}


async def test_httpx_mode_raises_on_f5(httpx_mock: HTTPXMock) -> None:
    """transport=httpx 撞 F5 → 明确抛 F5ChallengeError(不静默)。"""
    httpx_mock.add_response(
        url=PNCP_RE, status_code=200, content=F5_HTML, headers={"content-type": "text/html"}
    )
    async with PncpSession(transport="httpx", rate_limit_per_second=0) as s:
        with pytest.raises(F5ChallengeError):
            await s.get_json(URL)


async def test_browser_mode_uses_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps_mod, "BrowserSession", FakeBrowser)
    async with PncpSession(transport="browser", rate_limit_per_second=0) as s:
        assert s.backend == "browser"
        data = await s.get_json(URL)
    assert data == {"via": "browser"}


async def test_auto_falls_back_to_browser_on_f5(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    """transport=auto 起步 httpx,撞 F5 → 自动切 BrowserSession 重试该请求。"""
    monkeypatch.setattr(ps_mod, "BrowserSession", FakeBrowser)
    httpx_mock.add_response(
        url=PNCP_RE, status_code=200, content=F5_HTML, headers={"content-type": "text/html"}
    )
    async with PncpSession(transport="auto", rate_limit_per_second=0) as s:
        assert s.backend == "httpx"  # 起步 httpx
        data = await s.get_json(URL)
        assert s.backend == "browser"  # fallback 后变浏览器
    assert data == {"via": "browser"}


async def test_auto_stays_httpx_when_no_f5(httpx_mock: HTTPXMock) -> None:
    """transport=auto 不撞 F5 → 一直走 httpx(快)。"""
    httpx_mock.add_response(url=PNCP_RE, json={"via": "httpx"})
    async with PncpSession(transport="auto", rate_limit_per_second=0) as s:
        data = await s.get_json(URL)
        assert s.backend == "httpx"
    assert data == {"via": "httpx"}


def test_invalid_transport_falls_back_to_auto() -> None:
    s = PncpSession(transport="nonsense", rate_limit_per_second=0)
    assert s.transport == "auto"
