"""验证 HttpClient 的 F5 ASM 挑战检测 + on_challenge 回调链路。

不需要 Playwright,用一个假回调返回固定 cookies 就能覆盖整条路径。
"""
from __future__ import annotations

import re
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.core.exceptions import HttpError
from src.core.http_client import HttpClient, _is_f5_challenge

BASE_URL = "https://pncp.gov.br/api/consulta/v1"
ENDPOINT_RE = re.compile(r"https://pncp\.gov\.br/api/consulta/v1/.*")

# F5 BIG-IP ASM 挑战页特征片段
F5_CHALLENGE_HTML = (
    b'<!DOCTYPE html><html><head><meta http-equiv="Pragma" content="no-cache"/></head>\n'
    b'<script type="text/javascript">\n(function(){\n'
    b'window["bobcmn"] = "1011111010101020000000520000000520000000620000000120e0f68e5";\n'
    b"})();\n</script>\n</html>"
)

# F5 拒绝页(IP 黑名单分支),body 含 "Request Rejected"
F5_REJECT_HTML = (
    b"<html><head><title>Request Rejected</title></head>"
    b"<body>The requested URL was rejected. "
    b"Your support ID is: &lt;532466245821567504&gt;</body></html>"
)


# ─── _is_f5_challenge 检测器 ───────────────────────────────────────────


def test_is_f5_challenge_detects_bobcmn() -> None:
    """``window["bobcmn"]`` 出现在 text/html 中 → True。"""
    import httpx

    resp = httpx.Response(
        200,
        content=F5_CHALLENGE_HTML,
        headers={"content-type": "text/html"},
    )
    assert _is_f5_challenge(resp) is True


def test_is_f5_challenge_detects_request_rejected() -> None:
    """``Request Rejected`` 出现在 text/html 中 → True。"""
    import httpx

    resp = httpx.Response(
        200,
        content=F5_REJECT_HTML,
        headers={"content-type": "text/html"},
    )
    assert _is_f5_challenge(resp) is True


def test_is_f5_challenge_passes_json() -> None:
    """正常 application/json 响应 → False。"""
    import httpx

    resp = httpx.Response(
        200,
        json={"data": [], "totalRegistros": 0},
        headers={"content-type": "application/json"},
    )
    assert _is_f5_challenge(resp) is False


def test_is_f5_challenge_passes_innocent_html() -> None:
    """没有挑战特征的 HTML(比如 SPA 首页)→ False。"""
    import httpx

    resp = httpx.Response(
        200,
        content=b"<html><body>Welcome to PNCP</body></html>",
        headers={"content-type": "text/html"},
    )
    assert _is_f5_challenge(resp) is False


# ─── on_challenge 回调链路 ─────────────────────────────────────────────


async def test_challenge_triggers_callback_and_retries(httpx_mock: HTTPXMock) -> None:
    """首次拿到挑战页 → 调回调拿 cookies → 重试 → 拿到 JSON。"""
    # 第一次:F5 挑战
    httpx_mock.add_response(
        url=ENDPOINT_RE,
        status_code=200,
        content=F5_CHALLENGE_HTML,
        headers={"content-type": "text/html"},
    )
    # 第二次(挑战通过后):真实 JSON
    expected = {"data": [{"numeroControlePNCP": "abc"}], "totalRegistros": 1, "totalPaginas": 1}
    httpx_mock.add_response(url=ENDPOINT_RE, json=expected)

    callback_calls = 0

    async def fake_cb() -> dict[str, str]:
        nonlocal callback_calls
        callback_calls += 1
        return {"TS01ab23cd": "fake-cleared-token"}

    async with HttpClient(
        base_url=BASE_URL,
        rate_limit_per_second=0,
        on_challenge=fake_cb,
    ) as client:
        result = await client.get_json("/contratacoes/publicacao", params={"pagina": 1})

    assert result == expected
    assert callback_calls == 1

    # 验证第二次请求确实带上了回调返回的 cookie
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    second_cookie_header = requests[1].headers.get("cookie", "")
    assert "TS01ab23cd=fake-cleared-token" in second_cookie_header


async def test_no_callback_means_no_retry(httpx_mock: HTTPXMock) -> None:
    """没配 on_challenge 时,挑战页直接走 get_json 的 JSON decode 失败分支(返回 None)。"""
    httpx_mock.add_response(
        url=ENDPOINT_RE,
        status_code=200,
        content=F5_CHALLENGE_HTML,
        headers={"content-type": "text/html"},
    )

    async with HttpClient(base_url=BASE_URL, rate_limit_per_second=0) as client:
        result = await client.get_json("/contratacoes/publicacao")

    # JSON 解析失败 → get_json 返回 None;只发了一次请求(没有重试)
    assert result is None
    assert len(httpx_mock.get_requests()) == 1


async def test_raise_on_challenge_raises_f5_error(httpx_mock: HTTPXMock) -> None:
    """raise_on_challenge=True + 无回调 + 撞 F5 → 抛 F5ChallengeError(供 PncpSession auto 模式 fallback)。"""
    from src.core.exceptions import F5ChallengeError

    httpx_mock.add_response(
        url=ENDPOINT_RE,
        status_code=200,
        content=F5_CHALLENGE_HTML,
        headers={"content-type": "text/html"},
    )

    async with HttpClient(
        base_url=BASE_URL, rate_limit_per_second=0, raise_on_challenge=True
    ) as client:
        with pytest.raises(F5ChallengeError):
            await client.get("/contratacoes/publicacao")

    assert len(httpx_mock.get_requests()) == 1  # 不重试


async def test_challenge_still_present_after_refresh_raises(httpx_mock: HTTPXMock) -> None:
    """回调跑完了但服务端还是返回挑战页 → 抛 HttpError(不无限循环)。"""
    httpx_mock.add_response(
        url=ENDPOINT_RE,
        status_code=200,
        content=F5_CHALLENGE_HTML,
        headers={"content-type": "text/html"},
    )
    httpx_mock.add_response(
        url=ENDPOINT_RE,
        status_code=200,
        content=F5_CHALLENGE_HTML,
        headers={"content-type": "text/html"},
    )

    async def fake_cb() -> dict[str, str]:
        return {"TS01ab23cd": "still-no-good"}

    async with HttpClient(
        base_url=BASE_URL,
        rate_limit_per_second=0,
        on_challenge=fake_cb,
    ) as client:
        with pytest.raises(HttpError, match="F5 challenge still present"):
            await client.get("/contratacoes/publicacao")

    assert len(httpx_mock.get_requests()) == 2  # 一次原始 + 一次重试,不无限循环


async def test_callback_exception_wraps_as_httperror(httpx_mock: HTTPXMock) -> None:
    """回调本身抛异常 → 包成 HttpError 让上层 fetcher 跳过当前 modalidade。"""
    httpx_mock.add_response(
        url=ENDPOINT_RE,
        status_code=200,
        content=F5_CHALLENGE_HTML,
        headers={"content-type": "text/html"},
    )

    async def crashing_cb() -> dict[str, str]:
        raise RuntimeError("playwright not installed")

    async with HttpClient(
        base_url=BASE_URL,
        rate_limit_per_second=0,
        on_challenge=crashing_cb,
    ) as client:
        with pytest.raises(HttpError, match="on_challenge callback failed"):
            await client.get("/contratacoes/publicacao")


async def test_preloaded_cookies_are_sent(httpx_mock: HTTPXMock) -> None:
    """构造时传 cookies → 第一次请求就带上,免一次挑战往返。"""
    httpx_mock.add_response(url=ENDPOINT_RE, json={"ok": True})

    async with HttpClient(
        base_url=BASE_URL,
        rate_limit_per_second=0,
        cookies={"TSpre": "preloaded-value"},
    ) as client:
        await client.get_json("/contratacoes/publicacao")

    req = httpx_mock.get_requests()[0]
    assert "TSpre=preloaded-value" in req.headers.get("cookie", "")
