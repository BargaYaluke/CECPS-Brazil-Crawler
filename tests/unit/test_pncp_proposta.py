"""tests/unit/test_pncp_proposta.py — proposta fetcher 单测。

不依赖真实 PNCP,用 fake BrowserSession 注入预排响应。
覆盖关键差异:proposta 只有 ``dataFinal`` 一个日期参数,**没有** ``dataInicial``。
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from src.core.exceptions import HttpError
from src.fetchers import pncp_proposta
from src.fetchers.pncp_proposta import fetch_proposta, fetch_proposta_all

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class FakeBrowserSession:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses: deque[Any] = deque(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "FakeBrowserSession":
        self.entered = True
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.exited = True

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        parsed_qs = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        merged = {**parsed_qs, **(params or {})}
        self.calls.append({"url": url, "params": merged})
        if not self._responses:
            raise AssertionError(
                f"FakeBrowserSession: no more queued responses (call #{len(self.calls)})"
            )
        nxt = self._responses.popleft()
        if isinstance(nxt, HttpError):
            raise nxt
        return nxt


@pytest.fixture
def sample_envelope() -> dict:
    """复用 publicacao 的 fixture(proposta 返回 schema 跟 publicacao 相同)。"""
    return json.loads(
        (FIXTURE_DIR / "pncp_publicacao_sample.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def patch_session(monkeypatch: pytest.MonkeyPatch):
    def _make(responses: list[Any]) -> FakeBrowserSession:
        session = FakeBrowserSession(responses=responses)

        def _factory(*args: Any, **kwargs: Any) -> FakeBrowserSession:
            return session

        monkeypatch.setattr(pncp_proposta, "PncpSession", _factory)
        return session

    return _make


# ─── fetch_proposta 单页 ────────────────────────────────────────────────


async def test_fetch_proposta_only_data_final_no_data_inicial(
    sample_envelope: dict, tmp_path: Path
) -> None:
    """proposta 只有 dataFinal,**不应该** 出现 dataInicial。"""
    session = FakeBrowserSession(responses=[sample_envelope])

    result = await fetch_proposta(
        session,
        data_final="2026-06-01",
        modalidade_code=6,
        page=1,
        page_size=50,
        cache_root=tmp_path,
    )

    assert result == sample_envelope
    p = session.calls[0]["params"]
    assert p["dataFinal"] == "20260601"
    assert p["codigoModalidadeContratacao"] == 6
    assert p["pagina"] == 1
    assert p["tamanhoPagina"] == 50
    # 关键:proposta 不传 dataInicial
    assert "dataInicial" not in p

    # 缓存路径用 data_final 作为日期目录
    cache_file = tmp_path / "proposta" / "2026-06-01" / "modalidade_6_page_1.json"
    assert cache_file.exists()


async def test_fetch_proposta_404_returns_none(tmp_path: Path) -> None:
    session = FakeBrowserSession(
        responses=[HttpError("HTTP 404", status_code=404, url="...")]
    )
    result = await fetch_proposta(
        session, "2026-06-01", modalidade_code=6, page=99, cache_root=tmp_path
    )
    assert result is None


async def test_fetch_proposta_204_returns_none(tmp_path: Path) -> None:
    session = FakeBrowserSession(responses=[None])
    result = await fetch_proposta(
        session, "2026-06-01", modalidade_code=6, page=1, cache_root=tmp_path
    )
    assert result is None
    assert not (tmp_path / "proposta").exists()


# ─── fetch_proposta_all 遍历 ────────────────────────────────────────────


async def test_fetch_proposta_all_iterates_modalidades(
    sample_envelope: dict, tmp_path: Path, patch_session
) -> None:
    """两个 modalidade × 各 1 页 = 4 条。"""
    single = {**sample_envelope, "totalPaginas": 1}
    session = patch_session([single, single])

    records = []
    async for rec in fetch_proposta_all(
        "2026-06-01", modalidade_codes=[6, 4], cache_root=tmp_path
    ):
        records.append(rec)

    assert len(records) == 4
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["codigoModalidadeContratacao"] == 6
    assert session.calls[1]["params"]["codigoModalidadeContratacao"] == 4
    # 两个 modalidade 的 dataFinal 都一致
    for c in session.calls:
        assert c["params"]["dataFinal"] == "20260601"


async def test_fetch_proposta_all_iterates_pages(
    sample_envelope: dict, tmp_path: Path, patch_session
) -> None:
    page1 = {**sample_envelope, "totalPaginas": 2, "numeroPagina": 1}
    page2 = {**sample_envelope, "totalPaginas": 2, "numeroPagina": 2}
    session = patch_session([page1, page2])

    records = []
    async for rec in fetch_proposta_all(
        "2026-06-01", modalidade_codes=[6], cache_root=tmp_path
    ):
        records.append(rec)

    assert len(records) == 4
    assert session.calls[0]["params"]["pagina"] == 1
    assert session.calls[1]["params"]["pagina"] == 2

    cache_dir = tmp_path / "proposta" / "2026-06-01"
    assert (cache_dir / "modalidade_6_page_1.json").exists()
    assert (cache_dir / "modalidade_6_page_2.json").exists()


async def test_fetch_proposta_all_empty_first_page_skips(
    tmp_path: Path, patch_session
) -> None:
    """第一页空 → 跳过当前 modalidade,继续下一个。"""
    empty = {"data": [], "totalRegistros": 0, "totalPaginas": 0}
    has_data = {
        "data": [{"numeroControlePNCP": "x-1-001/2026"}],
        "totalRegistros": 1,
        "totalPaginas": 1,
    }
    session = patch_session([empty, has_data])

    records = []
    async for rec in fetch_proposta_all(
        "2026-06-01", modalidade_codes=[6, 4], cache_root=tmp_path
    ):
        records.append(rec)

    # modalidade=6 空跳过,modalidade=4 拿到 1 条
    assert len(records) == 1
    assert session.calls[1]["params"]["codigoModalidadeContratacao"] == 4
