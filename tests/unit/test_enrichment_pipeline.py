"""tests/unit/test_enrichment_pipeline.py — run_enrichment(region + fx)端到端。

用内存 SQLite + monkeypatch 让 orchestrator 的 ``session_scope()``/``init_db()``
落到内存库;HTTP 用 pytest_httpx mock。lite 版只保留 region/fx 两块富化。
"""
from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.pipeline import orchestrator as orch
from src.storage import (
    Base,
    Contratacao,
    ContratacaoIn,
    ContratacaoRepository,
)


@pytest.fixture
def memory_engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def isolated_engine(monkeypatch: pytest.MonkeyPatch, memory_engine):
    """让 orchestrator 的无参 session_scope()/init_db() 走内存库。"""
    monkeypatch.setattr("src.storage.database.get_engine", lambda *a, **kw: memory_engine)
    monkeypatch.setattr("src.pipeline.orchestrator.init_db", lambda: None)
    return memory_engine


FX_URL = re.compile(r"https://economia\.awesomeapi\.com\.br/json/last/BRL-CNY")


async def test_run_enrichment_region_fx(isolated_engine, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=FX_URL, json={"BRLCNY": {"bid": "1.5"}})

    with Session(isolated_engine) as s:
        repo = ContratacaoRepository(s)
        repo.upsert(ContratacaoIn(pncp_id="x-1-1/2026", uf_sigla="SP", valor_total_estimado=1000.0))
        repo.upsert(ContratacaoIn(pncp_id="x-1-2/2026", uf_sigla="AM", valor_total_estimado=2000.0))
        repo.upsert(ContratacaoIn(pncp_id="x-1-3/2026", uf_sigla="ZZ", valor_total_estimado=None))
        s.commit()

    counters = await orch.run_enrichment()

    assert counters["fx_rate"] == pytest.approx(1.5)
    assert counters["region_filled"] == 2  # SP, AM 命中;ZZ 未知不填
    assert counters["fx_filled"] == 2  # 两条有金额

    with Session(isolated_engine) as s:
        sp = s.get(Contratacao, "x-1-1/2026")
        assert sp.region_macro == "Sudeste"
        assert sp.region_gdp_tier == "high"
        assert sp.valor_cny_estimado == pytest.approx(1500.0)
        zz = s.get(Contratacao, "x-1-3/2026")
        assert zz.region_macro is None  # 未知 UF 不填


async def test_run_enrichment_only_region(isolated_engine, httpx_mock: HTTPXMock) -> None:
    """只跑 region → 不发任何 HTTP(fx 关掉),fx_filled=0。"""
    with Session(isolated_engine) as s:
        ContratacaoRepository(s).upsert(
            ContratacaoIn(pncp_id="x-2-1/2026", uf_sigla="SP", valor_total_estimado=1000.0)
        )
        s.commit()

    counters = await orch.run_enrichment(do_region=True, do_fx=False)
    assert counters["region_filled"] == 1
    assert counters["fx_filled"] == 0
    assert counters["fx_rate"] is None
    assert len(httpx_mock.get_requests()) == 0  # fx 关掉 → 无网络
