"""统一 HTTP 客户端。

封装 ``httpx.AsyncClient``,内置:

* 指数退避重试(``tenacity``,默认 3 次,base=2s,封顶 30s)
* 简单令牌桶限流(默认 10 req/s,可配置)
* 默认 30s 超时
* ``User-Agent: BrazilProcurementBot/1.0``
* **F5 BIG-IP ASM 挑战自动处理**:检测到 JS 挑战页时,触发 ``on_challenge``
  回调拿新 cookies 后重试一次(回调由调用方传入,核心层不强依赖 playwright)

所有 fetcher 必须经此发请求,禁止直接使用 httpx / requests。
"""
from __future__ import annotations

import asyncio
import json as _jsonlib
import time
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .exceptions import F5ChallengeError, HttpError, RateLimitExceededError
from .logger import logger
from .settings import get_http_settings


# ─── F5 ASM JS 挑战检测 ─────────────────────────────────────────────────


def _is_f5_challenge(resp: httpx.Response) -> bool:
    """判断响应是不是 F5 BIG-IP ASM 的 JS 挑战页。

    特征:
        - content-type 含 ``html``
        - body 前 2 KB 含 ``window["bobcmn"]`` 或 ``Request Rejected``

    Args:
        resp: ``httpx.Response`` 对象。

    Returns:
        命中则 ``True``。
    """
    ct = resp.headers.get("content-type", "").lower()
    if "html" not in ct:
        return False
    body_head = resp.text[:2048]
    return ('window["bobcmn"]' in body_head) or ("Request Rejected" in body_head)


# ─── 令牌桶限流 ────────────────────────────────────────────────────────


class _AsyncRateLimiter:
    """单实例进程内令牌桶,按"每秒 N 个"放行。

    够用即可;真要做多进程限流时换 redis 令牌桶。
    """

    def __init__(self, rate_per_second: float) -> None:
        self._interval = 1.0 / rate_per_second if rate_per_second > 0 else 0.0
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """阻塞直到下一次放行时间到达。"""
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


# ─── HttpClient ────────────────────────────────────────────────────────


ChallengeCallback = Callable[[], Awaitable[dict[str, str]]]


class HttpClient:
    """异步 HTTP 客户端,封装重试 / 限流 / 默认 header / F5 挑战自处理。

    使用上下文管理器进入 / 退出会话::

        async with HttpClient(on_challenge=fetch_pncp_cookies) as client:
            data = await client.get_json(
                "/api/consulta/v1/contratacoes/publicacao",
                params={"dataInicial": "20260501", "dataFinal": "20260527"},
            )

    Attributes:
        base_url: API 根地址,默认从 ``config/settings.yaml`` 的 ``http.base_url`` 读取。
        timeout: 单次请求超时(秒)。
        max_retries: 最大重试次数(指数退避)。
        user_agent: 标识用 User-Agent。
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        rate_limit_per_second: float | None = None,
        max_retries: int | None = None,
        user_agent: str | None = None,
        cookies: dict[str, str] | None = None,
        on_challenge: ChallengeCallback | None = None,
        raise_on_challenge: bool = False,
    ) -> None:
        cfg = get_http_settings()
        self.base_url: str = base_url if base_url is not None else cfg["base_url"]
        self.timeout: float = float(timeout if timeout is not None else cfg["timeout"])
        self.max_retries: int = int(max_retries if max_retries is not None else cfg["max_retries"])
        self.user_agent: str = user_agent if user_agent is not None else cfg["user_agent"]
        rate = (
            rate_limit_per_second
            if rate_limit_per_second is not None
            else cfg["rate_limit_per_second"]
        )
        self._limiter = _AsyncRateLimiter(float(rate))
        self._initial_cookies: dict[str, str] = dict(cookies) if cookies else {}
        self._on_challenge: ChallengeCallback | None = on_challenge
        self._raise_on_challenge: bool = raise_on_challenge
        self._challenge_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    # ─── 上下文管理 ─────────────────────────────────────────────────
    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        if self._initial_cookies:
            self._client.cookies.update(self._initial_cookies)
        logger.bind(
            base_url=self.base_url,
            timeout=self.timeout,
            cookies_preloaded=len(self._initial_cookies),
            challenge_callback=self._on_challenge is not None,
        ).info("http.client.open")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("http.client.close")

    # ─── 请求 ───────────────────────────────────────────────────────
    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET 请求,经过限流 + 重试 + F5 挑战自处理。

        流程:
            1. 走 tenacity 重试链(只对 5xx / 网络错 / 429)。
            2. 检查响应是不是 F5 ASM JS 挑战页。
            3. 若是且配了 ``on_challenge``,await 回调取新 cookies,
               更新 ``httpx.AsyncClient.cookies``,**重试一次**(不再走第 1 步)。
            4. 若挑战仍在,抛 ``HttpError``。

        Args:
            url: 相对或绝对 URL。
            **kwargs: 透传给 ``httpx`` 的参数(``params`` / ``headers`` / ...)。

        Returns:
            ``httpx.Response``。

        Raises:
            HttpError: 重试耗尽 / 4xx / F5 挑战刷新后仍未通过。
            RateLimitExceededError: 收到 429 时抛出(也是 HttpError 子类)。
        """
        if self._client is None:
            raise HttpError("HttpClient must be used as an async context manager")

        resp = await self._get_once_with_tenacity(url, **kwargs)

        # F5 ASM JS 挑战
        if not _is_f5_challenge(resp):
            return resp

        if self._on_challenge is None:
            # 无 cookie 回调:要么抛(让 PncpSession auto 模式 fallback 浏览器),
            # 要么保留旧契约(返回挑战页 → get_json 解析失败成 None)。
            if self._raise_on_challenge:
                raise F5ChallengeError(
                    "F5 BIG-IP ASM challenge detected on httpx transport; "
                    "需切真浏览器(pncp.transport=browser 或 auto)",
                    status_code=resp.status_code,
                    url=url,
                )
            return resp

        async with self._challenge_lock:
            log = logger.bind(url=url, status=resp.status_code, body_len=len(resp.content))
            log.warning("http.f5_challenge_detected")
            try:
                new_cookies = await self._on_challenge()
            except Exception as exc:
                raise HttpError(
                    f"on_challenge callback failed: {exc}",
                    status_code=resp.status_code,
                    url=url,
                ) from exc

            if new_cookies:
                assert self._client is not None
                self._client.cookies.update(new_cookies)
                log.bind(cookie_count=len(new_cookies)).info("http.f5_cookies_refreshed")
            else:
                log.warning("http.f5_callback_returned_empty_cookies")

            resp = await self._get_once_with_tenacity(url, **kwargs)
            if _is_f5_challenge(resp):
                raise HttpError(
                    "F5 challenge still present after cookie refresh",
                    status_code=resp.status_code,
                    url=url,
                )
            log.info("http.f5_challenge_passed")
            return resp

    async def _get_once_with_tenacity(self, url: str, **kwargs: Any) -> httpx.Response:
        """走完整 tenacity 重试链的单次 GET,不做 F5 检测。"""

        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(
                (httpx.TransportError, httpx.HTTPStatusError, RateLimitExceededError)
            ),
            reraise=True,
        )
        async def _do_get() -> httpx.Response:
            await self._limiter.acquire()
            logger.bind(method="GET", url=url).info("http.request")
            assert self._client is not None
            resp = await self._client.get(url, **kwargs)
            if resp.status_code == 429:
                # 限流 → 走 tenacity 重试
                raise RateLimitExceededError(
                    "429 Too Many Requests",
                    status_code=429,
                    url=str(resp.request.url),
                )
            if 500 <= resp.status_code < 600:
                # 5xx → 服务端临时错,走 tenacity 重试
                resp.raise_for_status()
            if 400 <= resp.status_code < 500:
                # 4xx(404 等)→ 不重试,抛 HttpError
                raise HttpError(
                    f"HTTP {resp.status_code} on {resp.request.url}",
                    status_code=resp.status_code,
                    url=str(resp.request.url),
                )
            return resp

        try:
            return await _do_get()
        except RateLimitExceededError:
            raise
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise HttpError(
                f"GET {url} failed after retries: {exc}",
                status_code=status,
                url=url,
            ) from exc

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        """GET 并直接返回解析后的 JSON。

        宽松解析:204 No Content / 空 body / 仅空白 / 非 JSON 内容,
        都统一返回 ``None`` 并打 warning(PNCP 在无结果时常这样返回)。
        """
        resp = await self.get(url, **kwargs)
        if resp.status_code == 204 or not resp.content or not resp.content.strip():
            logger.bind(status=resp.status_code, url=str(resp.request.url)).warning(
                "http.empty_body"
            )
            return None
        try:
            return resp.json()
        except _jsonlib.JSONDecodeError:
            logger.bind(
                status=resp.status_code,
                url=str(resp.request.url),
                content_type=resp.headers.get("content-type"),
                content_preview=resp.content[:200].decode("utf-8", errors="replace"),
            ).warning("http.json_decode_failed")
            return None


__all__ = ["HttpClient", "ChallengeCallback"]
