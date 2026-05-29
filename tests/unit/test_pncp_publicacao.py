"""Unit tests for ``src.fetchers.pncp_publicacao``。

不真启动 Chromium — 用 ``FakeBrowserSession`` 把响应序列预编好,
验证 fetcher 的参数拼装、分页、缓存落地、404 / 空 body 跳过等逻辑。
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from src.core.exceptions import HttpError
from src.fetchers import pncp_publicacao
from src.fetchers.pncp_publicacao import fetch_publicacao, fetch_publicacao_all

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


# ─── Fake BrowserSession ───────────────────────────────────────────────


class FakeBrowserSession:
    """假的 BrowserSession,实现 ``__aenter__/__aexit__/get_json`` 三件套。

    用 deque 预排响应序列(每次 ``get_json`` 出一个):
        * ``dict`` → 直接作为 JSON 返回
        * ``None`` → 模拟 204 / 空 body
        * ``HttpError`` 实例 → 抛出(模拟 4xx)
        * callable → 调用 ``fn(url, params)`` 后用返回值

    所有请求被记录到 ``calls`` 列表,字段含 ``url`` / ``params`` / ``page`` / ``modalidade``。
    """

    def __init__(self, responses: list[Any] | None = None, *args: Any, **kwargs: Any) -> None:
        self._responses: deque[Any] = deque(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "FakeBrowserSession":
        self.entered = True
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.exited = True

    async def get_json(
        self, url: str, params: dict[str, Any] | None = None
    ) -> Any:
        # 记录调用
        parsed_qs = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        merged = {**parsed_qs, **(params or {})}
        self.calls.append({"url": url, "params": merged})

        if not self._responses:
            raise AssertionError(
                f"FakeBrowserSession: no more pre-queued responses (call #{len(self.calls)} url={url})"
            )
        nxt = self._responses.popleft()
        if isinstance(nxt, HttpError):
            raise nxt
        if callable(nxt):
            return nxt(url, params)
        return nxt


@pytest.fixture
def sample_envelope() -> dict:
    """fixture: 真实 PNCP 响应骨架(2 条 record)。"""
    return json.loads(
        (FIXTURE_DIR / "pncp_publicacao_sample.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def empty_envelope() -> dict:
    return {
        "data": [],
        "totalRegistros": 0,
        "totalPaginas": 0,
        "numeroPagina": 1,
        "paginasRestantes": 0,
        "empty": True,
    }


@pytest.fixture
def patch_session(monkeypatch: pytest.MonkeyPatch):
    """工厂:返回一个能注入到 fetch_publicacao_all 的 fake session creator。

    用法::

        session = patch_session(responses=[envelope1, envelope2])
        async for rec in fetch_publicacao_all(...):
            ...
        # 之后可以读 session.calls 做断言
    """

    def _make(responses: list[Any]) -> FakeBrowserSession:
        session = FakeBrowserSession(responses=responses)

        # 让 fetch_publicacao_all 里 `async with PncpSession(...)` 拿到这个 fake
        def _factory(*args: Any, **kwargs: Any) -> FakeBrowserSession:
            return session

        monkeypatch.setattr(pncp_publicacao, "PncpSession", _factory)
        return session

    return _make


# ─── fetch_publicacao 单页 ──────────────────────────────────────────────


async def test_fetch_publicacao_single_page_writes_cache(
    sample_envelope: dict, tmp_path: Path
) -> None:
    session = FakeBrowserSession(responses=[sample_envelope])

    result = await fetch_publicacao(
        session,
        "2026-05-26",
        "2026-05-26",
        modalidade_code=6,
        page=1,
        page_size=50,
        cache_root=tmp_path,
    )

    assert result == sample_envelope

    # 校验请求参数
    assert len(session.calls) == 1
    p = session.calls[0]["params"]
    assert p["dataInicial"] == "20260526"
    assert p["dataFinal"] == "20260526"
    assert p["codigoModalidadeContratacao"] == 6
    assert p["pagina"] == 1
    assert p["tamanhoPagina"] == 50

    # 校验缓存落地
    cache_file = tmp_path / "publicacao" / "2026-05-26" / "modalidade_6_page_1.json"
    assert cache_file.exists()
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached == sample_envelope


async def test_fetch_publicacao_clamps_page_size_to_50(sample_envelope: dict) -> None:
    """page_size > 50 会被夹到 50(PNCP /contratacoes/* 上限,>50 报 400)。"""
    session = FakeBrowserSession(responses=[sample_envelope])
    await fetch_publicacao(
        session, "2026-05-26", "2026-05-26", modalidade_code=6, page=1, page_size=500
    )
    assert session.calls[0]["params"]["tamanhoPagina"] == 50


async def test_fetch_publicacao_404_returns_none(tmp_path: Path) -> None:
    """4xx(404)→ 静默返回 None,不抛错。"""
    session = FakeBrowserSession(
        responses=[HttpError("HTTP 404", status_code=404, url="...")]
    )

    result = await fetch_publicacao(
        session,
        "2026-05-26",
        "2026-05-26",
        modalidade_code=6,
        page=999,
        cache_root=tmp_path,
    )

    assert result is None
    assert len(session.calls) == 1


async def test_fetch_publicacao_204_no_content_returns_none(tmp_path: Path) -> None:
    """空 body(204)→ get_json 返回 None → fetcher 返回 None。"""
    session = FakeBrowserSession(responses=[None])

    result = await fetch_publicacao(
        session,
        "2026-05-26",
        "2026-05-26",
        modalidade_code=6,
        page=1,
        cache_root=tmp_path,
    )

    assert result is None
    # 缓存不写
    assert not (tmp_path / "publicacao").exists()


async def test_fetch_publicacao_no_cache_when_cache_root_none(
    sample_envelope: dict, tmp_path: Path
) -> None:
    session = FakeBrowserSession(responses=[sample_envelope])

    result = await fetch_publicacao(
        session,
        "2026-05-26",
        "2026-05-26",
        modalidade_code=6,
        page=1,
        cache_root=None,
    )

    assert result == sample_envelope
    assert list(tmp_path.iterdir()) == []


async def test_fetch_publicacao_non_404_http_error_propagates(tmp_path: Path) -> None:
    """非 404 的 HttpError(500 等)向上传播。"""
    session = FakeBrowserSession(
        responses=[HttpError("HTTP 500", status_code=500, url="...")]
    )

    with pytest.raises(HttpError, match="HTTP 500"):
        await fetch_publicacao(
            session,
            "2026-05-26",
            "2026-05-26",
            modalidade_code=6,
            page=1,
            cache_root=tmp_path,
        )


# ─── fetch_publicacao_all 多页迭代 ──────────────────────────────────────


async def test_fetch_publicacao_all_iterates_two_pages(
    sample_envelope: dict, tmp_path: Path, patch_session
) -> None:
    page1 = {**sample_envelope, "numeroPagina": 1, "totalPaginas": 2, "paginasRestantes": 1}
    page2 = {**sample_envelope, "numeroPagina": 2, "totalPaginas": 2, "paginasRestantes": 0}
    session = patch_session([page1, page2])

    records = []
    async for record in fetch_publicacao_all(
        "2026-05-26",
        "2026-05-26",
        modalidade_codes=[6],
        page_size=50,
        cache_root=tmp_path,
    ):
        records.append(record)

    # 2 条 × 2 页 = 4 条
    assert len(records) == 4
    # 两次请求,page=1 / page=2
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["pagina"] == 1
    assert session.calls[1]["params"]["pagina"] == 2
    # 进出 session
    assert session.entered and session.exited

    # 缓存两个文件都在
    cache_dir = tmp_path / "publicacao" / "2026-05-26"
    assert (cache_dir / "modalidade_6_page_1.json").exists()
    assert (cache_dir / "modalidade_6_page_2.json").exists()


async def test_fetch_publicacao_all_empty_page_breaks(
    empty_envelope: dict, tmp_path: Path, patch_session
) -> None:
    session = patch_session([empty_envelope])

    records = []
    async for record in fetch_publicacao_all(
        "2026-05-26",
        "2026-05-26",
        modalidade_codes=[6],
        page_size=50,
        cache_root=tmp_path,
    ):
        records.append(record)

    assert records == []
    # 空响应后不应该再翻页
    assert len(session.calls) == 1


async def test_fetch_publicacao_all_404_skips_modalidade(
    sample_envelope: dict, tmp_path: Path, patch_session
) -> None:
    """modalidade=6 → 404 跳过;modalidade=4 → 正常 2 条。"""
    single_page = {**sample_envelope, "totalPaginas": 1}
    session = patch_session(
        [
            HttpError("HTTP 404", status_code=404, url="..."),
            single_page,
        ]
    )

    records = []
    async for record in fetch_publicacao_all(
        "2026-05-26",
        "2026-05-26",
        modalidade_codes=[6, 4],
        page_size=50,
        cache_root=tmp_path,
    ):
        records.append(record)

    assert len(records) == 2  # 只来自 modalidade=4
    assert len(session.calls) == 2
    # 第二次请求的 modalidade 是 4
    assert session.calls[1]["params"]["codigoModalidadeContratacao"] == 4
