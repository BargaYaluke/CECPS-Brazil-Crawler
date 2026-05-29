"""Playwright 真浏览器封装(全程浏览器模式)。

PNCP 的 ``/api/consulta/v1/*`` 与 ``/api/search/*`` 前面挂着
**F5 BIG-IP ASM Bot Defense**(TSPD)JavaScript 挑战,而且 F5 把
通过挑战后的 ``TS*`` cookies **绑定到了 TLS 会话指纹**:
即使把 cookies 从 Chrome 偷出来注入到 httpx,F5 一查 TLS 握手
不是 Chrome 的,照样拒(243 字节 ``Request Rejected``)。

业界标准方案是:**全程使用真浏览器**。本模块提供两个层次的封装。

两个公共入口
-------------

* :func:`fetch_pncp_cookies` — 一次性启动浏览器,解挑战,导出 cookies。
  保留是为了向后兼容 / 给将来 :class:`HttpClient` 在非 anti-bot
  站点(BrasilAPI / IBGE)上使用。

* :class:`BrowserSession` — **长期会话**。启动浏览器并解完挑战后,
  保留 page 对象,所有 GET 通过 ``page.evaluate('fetch(...)')``
  在浏览器内发起,UA / TLS 指纹 / cookies 全部跟浏览器一致,
  F5 完全识别不出来。这是 PNCP fetcher 实际用的方式。
"""
from __future__ import annotations

import asyncio
import json as _jsonlib
import time
from types import TracebackType
from typing import Any
from urllib.parse import urlencode

from .exceptions import BrowserError, HttpError, RateLimitExceededError
from .logger import logger
from .settings import get_browser_settings


# ─── 一次性 cookies 抽取(保留兼容,实际不再被 PNCP fetcher 使用) ─────


async def fetch_pncp_cookies(
    challenge_url: str | None = None,
    *,
    headless: bool | None = None,
    wait_after_load_ms: int | None = None,
    navigation_timeout_ms: int | None = None,
    user_agent: str | None = None,
    cookie_name_prefixes: list[str] | None = None,
) -> dict[str, str]:
    """启动 headless Chromium 解 F5 JS 挑战,返回通过后的 cookies。

    **注意**:F5 的 ``TS*`` cookies 跟 TLS 会话绑定,把它们拷到
    httpx 这种独立 HTTP 客户端用,F5 仍会拒。这函数留下来主要给
    非 anti-bot 站点(BrasilAPI / IBGE)使用,以及兼容老代码。
    PNCP 抓取要用 :class:`BrowserSession`。
    """
    cfg = get_browser_settings()
    challenge_url = challenge_url or cfg["challenge_url"]
    headless = cfg["headless"] if headless is None else headless
    wait_ms = cfg["wait_after_load_ms"] if wait_after_load_ms is None else wait_after_load_ms
    nav_timeout = (
        cfg["navigation_timeout_ms"] if navigation_timeout_ms is None else navigation_timeout_ms
    )
    ua = user_agent or cfg["user_agent"]
    prefixes = cfg["cookie_name_prefixes"] if cookie_name_prefixes is None else cookie_name_prefixes
    prefixes_tuple = tuple(prefixes) if prefixes else ()

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserError(
            "playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc

    log = logger.bind(challenge_url=challenge_url, headless=headless, wait_ms=wait_ms)
    log.info("browser.challenge.start")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            try:
                context = await browser.new_context(user_agent=ua)
                page = await context.new_page()
                page.set_default_navigation_timeout(nav_timeout)
                await page.goto(challenge_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(wait_ms)
                raw_cookies: list[dict[str, Any]] = await context.cookies()
            finally:
                await browser.close()
    except BrowserError:
        raise
    except Exception as exc:
        raise BrowserError(f"playwright failed: {exc}") from exc

    if prefixes_tuple:
        filtered = {
            c["name"]: c["value"]
            for c in raw_cookies
            if any(c["name"].startswith(p) for p in prefixes_tuple)
        }
    else:
        filtered = {c["name"]: c["value"] for c in raw_cookies}

    log.bind(
        cookie_count_all=len(raw_cookies),
        cookie_count_kept=len(filtered),
        cookie_names=list(filtered.keys()),
    ).info("browser.challenge.done")

    if prefixes_tuple and not filtered:
        raise BrowserError(
            f"no cookies matching prefixes {prefixes_tuple} after {wait_ms} ms; "
            f"got {len(raw_cookies)} other cookies"
        )
    return filtered


# ─── 限流 ──────────────────────────────────────────────────────────────


class _AsyncRateLimiter:
    """单实例进程内令牌桶,按"每秒 N 个"放行。"""

    def __init__(self, rate_per_second: float) -> None:
        self._interval = 1.0 / rate_per_second if rate_per_second > 0 else 0.0
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self._interval - now
            if wait > 0:
                await asyncio.sleep(wait)
                self._last = time.monotonic()
            else:
                self._last = now


# ─── 浏览器内 fetch 的 JS 片段 ──────────────────────────────────────────


_IN_BROWSER_FETCH_JS = """
async (req) => {
    try {
        const resp = await fetch(req.url, {
            method: 'GET',
            headers: { 'Accept': 'application/json, text/plain, */*' },
            credentials: 'include',
        });
        const text = await resp.text();
        const headers = {};
        resp.headers.forEach((v, k) => { headers[k] = v; });
        return { status: resp.status, headers: headers, body: text };
    } catch (e) {
        return { status: 0, headers: {}, body: String(e) };
    }
}
"""


# ─── 长期浏览器会话 ────────────────────────────────────────────────────


class BrowserSession:
    """长期保持的浏览器会话,通过 ``page.evaluate('fetch(...)')`` 发请求。

    用法::

        async with BrowserSession() as session:
            data = await session.get_json(
                "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao",
                params={"dataInicial": "20260526", "dataFinal": "20260526",
                        "codigoModalidadeContratacao": 6,
                        "pagina": 1, "tamanhoPagina": 50},
            )

    内部流程:
        1. ``__aenter__`` — 启动 Chromium → new context → new page →
           跳转到 SPA 入口 → 等 F5 JS 挑战自动跑完。
        2. ``get_json`` — 用 ``page.evaluate``,在浏览器 JS 里执行 ``fetch()``。
           走的就是浏览器自己的网络栈,F5 看到的请求跟正常 SPA 一模一样,
           完全识别不出来。
        3. ``__aexit__`` — 关浏览器。

    线程安全:同一 ``BrowserSession`` 实例不要在多协程并发用
    ``get_json``(同一 page 不能并发 evaluate)— 内部加了锁强制串行。
    fetcher 本身就是单协程顺序遍历,够用。

    Attributes:
        challenge_url: 启动时访问的 SPA URL。
        headless: 是否无头模式。
        rate_limit_per_second: 客户端侧的请求速率上限。
    """

    def __init__(
        self,
        challenge_url: str | None = None,
        *,
        headless: bool | None = None,
        wait_after_load_ms: int | None = None,
        navigation_timeout_ms: int | None = None,
        user_agent: str | None = None,
        rate_limit_per_second: float = 5.0,
    ) -> None:
        cfg = get_browser_settings()
        self.challenge_url: str = challenge_url or cfg["challenge_url"]
        self.headless: bool = cfg["headless"] if headless is None else headless
        self._wait_ms: int = (
            cfg["wait_after_load_ms"] if wait_after_load_ms is None else wait_after_load_ms
        )
        self._nav_timeout: int = (
            cfg["navigation_timeout_ms"]
            if navigation_timeout_ms is None
            else navigation_timeout_ms
        )
        self._ua: str = user_agent or cfg["user_agent"]
        self._limiter = _AsyncRateLimiter(rate_limit_per_second)
        self._page_lock = asyncio.Lock()

        # Playwright 对象,在 __aenter__ 里赋值
        self._pw_cm: Any = None
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    async def __aenter__(self) -> "BrowserSession":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserError(
                "playwright is not installed. "
                "Run: pip install playwright && playwright install chromium"
            ) from exc

        log = logger.bind(
            challenge_url=self.challenge_url,
            headless=self.headless,
            wait_ms=self._wait_ms,
        )
        log.info("browser_session.start")

        try:
            self._pw_cm = async_playwright()
            self._pw = await self._pw_cm.__aenter__()
            self._browser = await self._pw.chromium.launch(headless=self.headless)
            self._context = await self._browser.new_context(user_agent=self._ua)
            self._page = await self._context.new_page()
            self._page.set_default_navigation_timeout(self._nav_timeout)
            await self._page.goto(self.challenge_url, wait_until="domcontentloaded")
            await self._page.wait_for_timeout(self._wait_ms)
        except BrowserError:
            await self._cleanup()
            raise
        except Exception as exc:
            await self._cleanup()
            raise BrowserError(f"failed to bootstrap browser session: {exc}") from exc

        # 解挑战是否成功:看 cookie jar 里是否有 TS* cookie
        cookies = await self._context.cookies()
        ts_count = sum(1 for c in cookies if c["name"].startswith("TS"))
        log.bind(cookie_count=len(cookies), ts_cookie_count=ts_count).info(
            "browser_session.ready"
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._cleanup()

    async def _cleanup(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._pw_cm is not None:
                await self._pw_cm.__aexit__(None, None, None)
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None
        self._pw_cm = None
        logger.info("browser_session.closed")

    @staticmethod
    def _build_full_url(url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{urlencode(params, doseq=True)}"

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """在浏览器内 fetch 一个 URL,返回 JSON(失败时返回 None)。

        Args:
            url: 绝对 URL(``page.evaluate`` 不走 base_url)。
            params: 查询参数;追加到 URL 上。

        Returns:
            解析后的 JSON;204 / 空 body / 非 JSON 返回 ``None``。

        Raises:
            HttpError: 4xx 抛(让 fetcher 跳过当前 modalidade);5xx 同理(不重试,
                简化设计:Playwright 自己有网络层重试,且服务端 500 多半要等)。
            RateLimitExceededError: 429。
            BrowserError: 浏览器层错误(page 已关 / JS 异常等)。
        """
        if self._page is None:
            raise BrowserError("BrowserSession must be used as an async context manager")

        full_url = self._build_full_url(url, params)
        await self._limiter.acquire()

        log = logger.bind(method="GET", url=full_url)
        log.info("browser_session.request")

        async with self._page_lock:
            try:
                result = await self._page.evaluate(
                    _IN_BROWSER_FETCH_JS, {"url": full_url}
                )
            except Exception as exc:
                raise BrowserError(f"page.evaluate failed: {exc}") from exc

        status = int(result.get("status", 0))
        headers_map: dict[str, str] = result.get("headers", {}) or {}
        body: str = result.get("body", "") or ""
        content_type = (headers_map.get("content-type") or "").lower()

        log.bind(status=status, content_type=content_type, body_len=len(body)).debug(
            "browser_session.response"
        )

        if status == 0:
            # window.fetch 抛了:网络层错(DNS / SSL / abort)
            raise BrowserError(f"in-browser fetch failed: {body[:200]}")

        if status == 429:
            raise RateLimitExceededError(
                "429 Too Many Requests", status_code=429, url=full_url
            )

        if 400 <= status < 500:
            raise HttpError(
                f"HTTP {status} on {full_url}", status_code=status, url=full_url
            )

        if 500 <= status < 600:
            raise HttpError(
                f"HTTP {status} on {full_url}", status_code=status, url=full_url
            )

        if status == 204 or not body or not body.strip():
            log.warning("browser_session.empty_body")
            return None

        if "json" not in content_type:
            log.bind(content_type=content_type, body_preview=body[:200]).warning(
                "browser_session.non_json_response"
            )
            return None

        try:
            return _jsonlib.loads(body)
        except _jsonlib.JSONDecodeError:
            log.bind(body_preview=body[:200]).warning(
                "browser_session.json_decode_failed"
            )
            return None


__all__ = ["fetch_pncp_cookies", "BrowserSession"]
