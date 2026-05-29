"""tests/unit/test_enrichment_pipeline.py — 目录存储 + run_catalogo + run_enrichment 端到端。

用内存 SQLite + monkeypatch ``get_engine`` 让 orchestrator 的 ``session_scope()``
落到内存库(沿用 test_pipeline_atualizacao 的隔离手法);HTTP 用 pytest_httpx mock。
"""
from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.http_client import HttpClient
from src.pipeline import orchestrator as orch
from src.storage import (
    Base,
    CatalogoRepository,
    Contratacao,
    ContratacaoIn,
    ContratacaoRepository,
    Orgao,
    catalogo_in_from_material,
    catalogo_in_from_servico,
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


# ─── CatalogoRepository ─────────────────────────────────────────────────


def test_catalogo_repository_upsert_idempotent(memory_engine) -> None:
    mat = {
        "codigoItem": 206504,
        "nomeGrupo": "MOBILIÁRIOS",
        "nomeClasse": "MOBILIÁRIO PARA ESCRITÓRIO",
        "nomePdm": "CADEIRA ESCRITÓRIO",
        "statusItem": True,
    }
    with Session(memory_engine) as s:
        repo = CatalogoRepository(s)
        repo.upsert(catalogo_in_from_material(mat))
        repo.upsert(catalogo_in_from_material(mat))  # 重复 → 幂等
        repo.upsert(
            catalogo_in_from_servico(
                {"codigoServico": 7250, "nomeClasse": "SERVIÇOS HOSPITALARES", "nomeServico": "ENDOSCOPIA"}
            )
        )
        s.commit()
        assert repo.count() == 2

        row = repo.get_by_codigo("206504")
        assert row is not None
        assert row.tipo == "material"
        assert row.nome_pdm == "CADEIRA ESCRITÓRIO"

        row2 = repo.get_by_codigo("7250", tipo="servico")
        assert row2 is not None
        assert row2.tipo == "servico"
        assert row2.descricao == "ENDOSCOPIA"


# ─── run_catalogo(fetcher → 入库)──────────────────────────────────────


MATERIAL_URL = re.compile(
    r"https://dadosabertos\.compras\.gov\.br/modulo-material/4_consultarItemMaterial.*"
)


async def test_run_catalogo_stores_material(isolated_engine, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=MATERIAL_URL,
        json={
            "resultado": [
                {"codigoItem": 1, "nomeClasse": "C1", "nomePdm": "P1"},
                {"codigoItem": 2, "nomeClasse": "C2"},
            ],
            "totalRegistros": 2,
            "totalPaginas": 1,
            "paginasRestantes": 0,
        },
    )
    counters = await orch.run_catalogo(tipo="material", page_size=10)
    assert counters["material"] == 2
    assert counters["total"] == 2
    with Session(isolated_engine) as s:
        assert CatalogoRepository(s).count() == 2


# ─── run_enrichment(region + fx + orgao + catalogo-itens)──────────────


FX_URL = re.compile(r"https://economia\.awesomeapi\.com\.br/json/last/BRL-CNY")


async def test_run_enrichment_region_fx_orgao(isolated_engine, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=FX_URL, json={"BRLCNY": {"bid": "1.5"}})

    # seed 3 条招标:SP/AM(已知 UF)、ZZ(未知);两条有金额
    with Session(isolated_engine) as s:
        repo = ContratacaoRepository(s)
        repo.upsert(
            ContratacaoIn(
                pncp_id="x-1-1/2026", uf_sigla="SP", orgao_cnpj="111",
                orgao_razao_social="ORG A", valor_total_estimado=1000.0,
            )
        )
        repo.upsert(
            ContratacaoIn(
                pncp_id="x-1-2/2026", uf_sigla="AM", orgao_cnpj="111",
                orgao_razao_social="ORG A", valor_total_estimado=2000.0,
            )
        )
        repo.upsert(
            ContratacaoIn(
                pncp_id="x-1-3/2026", uf_sigla="ZZ", orgao_cnpj="222",
                valor_total_estimado=None,
            )
        )
        s.commit()

    counters = await orch.run_enrichment(with_brasilapi=False)

    assert counters["fx_rate"] == pytest.approx(1.5)
    assert counters["region_filled"] == 2  # SP, AM 命中;ZZ 未知不填
    assert counters["fx_filled"] == 2  # 两条有金额
    assert counters["orgaos_upserted"] == 2  # cnpj 111, 222

    with Session(isolated_engine) as s:
        row = s.get(Contratacao, "x-1-1/2026")
        assert row.region_macro == "Sudeste"
        assert row.region_gdp_tier == "high"
        assert row.valor_cny_estimado == pytest.approx(1500.0)  # 1000 × 1.5

        zz = s.get(Contratacao, "x-1-3/2026")
        assert zz.region_macro is None  # 未知 UF 不填
        assert zz.valor_cny_estimado is None  # 无金额不填

        org = s.get(Orgao, "111")
        assert org.total_contratacoes_2y == 2
        assert org.total_valor_2y == pytest.approx(3000.0)
        assert org.nome == "ORG A"
        assert org.uf == "SP"  # max(SP, AM) = SP


async def test_run_enrichment_only_region(isolated_engine, httpx_mock: HTTPXMock) -> None:
    """只跑 region → 不发任何 HTTP(fx 关掉),fx_filled=0。"""
    with Session(isolated_engine) as s:
        ContratacaoRepository(s).upsert(
            ContratacaoIn(pncp_id="y-1-1/2026", uf_sigla="RJ", valor_total_estimado=500.0)
        )
        s.commit()

    counters = await orch.run_enrichment(
        do_region=True, do_fx=False, do_orgao=False, do_catalogo_itens=False
    )
    assert counters["region_filled"] == 1
    assert counters["fx_filled"] == 0
    assert counters["fx_rate"] is None
    assert len(httpx_mock.get_requests()) == 0  # fx 关掉 → 无网络
