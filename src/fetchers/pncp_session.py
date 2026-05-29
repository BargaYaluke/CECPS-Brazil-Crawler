"""PNCP 列表接口统一传输层(httpx 优先 / 真浏览器 fallback)。

``publicacao`` / ``atualizacao`` / ``proposta`` / ``contratos`` 这 4 个
``/api/consulta/v1/*`` 接口曾被 F5 BIG-IP ASM 拦,需全程真浏览器
(:class:`BrowserSession`)。**2026-05-28 实测 F5 撤了**,httpx 直连即可,
比浏览器快 5-10 倍(省掉浏览器启动 ~30s + 每页 JS 引擎开销)。

本类按 ``settings.yaml`` 的 ``pncp.transport`` 选传输,对外只暴露
``get_json(url, params)``(与 :class:`HttpClient` / :class:`BrowserSession` 同签名),
fetcher 用 ``async with PncpSession(...) as session:`` 即可,无感知底层:

    transport=httpx    只走 :class:`HttpClient`(``raise_on_challenge=True``);
                       撞 F5 抛 :class:`F5ChallengeError`(明确报错,不静默出错)
    transport=browser  全程 :class:`BrowserSession`(真浏览器,最稳;F5 复活时用)
    transport=auto     httpx 优先;``get_json`` 撞 :class:`F5ChallengeError` →
                       关 httpx、起 :class:`BrowserSession`、用浏览器重试该请求,
                       之后所有请求都走浏览器(**默认**)

注:F5 的 ``TS*`` cookies 绑 TLS 指纹,把 cookie 拷给 httpx 重放无效 —— 所以
fallback 是**整体切到真浏览器**,而不是 ``HttpClient`` 的 ``on_challenge`` cookie 回放。
"""
from __future__ import annotations

from types import TracebackType
from typing import Any

from ..core.browser_client import BrowserSession
from ..core.exceptions import F5ChallengeError
from ..core.http_client import HttpClient
from ..core.logger import logger
from ..core.settings import get_pncp_settings

_VALID_TRANSPORTS = ("auto", "httpx", "browser")


class PncpSession:
    """PNCP 列表接口的传输抽象;详见模块 docstring。

    Attributes:
        transport: ``auto`` / ``httpx`` / ``browser``。
        backend: 当前实际后端(``httpx`` / ``browser``);auto 模式 fallback 后会变。
    """

    def __init__(
        self,
        *,
        transport: str | None = None,
        rate_limit_per_second: float | None = None,
    ) -> None:
        cfg = get_pncp_settings()
        t = str(transport or cfg.get("transport", "auto")).lower()
        self.transport: str = t if t in _VALID_TRANSPORTS else "auto"
        self._rate: float = float(
            rate_limit_per_second
            if rate_limit_per_second is not None
            else cfg["rate_limit_per_sec"]
        )
        self._http: HttpClient | None = None
        self._browser: BrowserSession | None = None
        self.backend: str | None = None

    # ─── 上下文管理 ─────────────────────────────────────────────────
    async def __aenter__(self) -> "PncpSession":
        if self.transport == "browser":
            await self._open_browser()
        else:  # httpx / auto
            self._http = HttpClient(
                rate_limit_per_second=self._rate,
                raise_on_challenge=True,
            )
            await self._http.__aenter__()
            self.backend = "httpx"
            logger.bind(transport=self.transport).info("pncp_session.open_httpx")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._close_http()
        if self._browser is not None:
            try:
                await self._browser.__aexit__(exc_type, exc, tb)
            finally:
                self._browser = None

    # ─── 后端切换 ───────────────────────────────────────────────────
    async def _open_browser(self) -> None:
        self._browser = BrowserSession(rate_limit_per_second=self._rate)
        await self._browser.__aenter__()
        self.backend = "browser"
        logger.info("pncp_session.open_browser")

    async def _close_http(self) -> None:
        if self._http is not None:
            try:
                await self._http.__aexit__(None, None, None)
            finally:
                self._http = None

    # ─── 请求 ───────────────────────────────────────────────────────
    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET 并返回 JSON;auto 模式撞 F5 时透明切浏览器重试。

        Args:
            url: 绝对 URL(fetcher 自己拼好;两种后端都不依赖 base_url)。
            params: 查询参数。

        Returns:
            解析后的 JSON;空 / 非 JSON 返回 ``None``(同底层语义)。

        Raises:
            F5ChallengeError: ``transport=httpx`` 撞 F5 时抛(上层 fetcher 据此报错)。
            HttpError / BrowserError: 其它请求错误。
        """
        if self.backend == "browser":
            assert self._browser is not None
            return await self._browser.get_json(url, params=params)

        assert self._http is not None
        try:
            return await self._http.get_json(url, params=params)
        except F5ChallengeError:
            if self.transport != "auto":
                raise  # httpx 模式:明确报错
            logger.warning("pncp_session.f5_detected_fallback_browser")
            await self._close_http()
            await self._open_browser()
            assert self._browser is not None
            return await self._browser.get_json(url, params=params)


__all__ = ["PncpSession"]
