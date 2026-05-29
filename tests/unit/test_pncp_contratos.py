"""tests/unit/test_pncp_contratos.py — contratos fetcher + storage 单测。

覆盖:
- fetcher 不传 modalidade(合同没这个维度)
- page_size 被自动夹到 ≥ 10
- 真实 fixture(22KB,10 条)能被 ContratoRaw 校验通过
- to_contrato_in 扁平化嵌套对象正确
- ContratoRepository UPSERT 幂等
- 按 fornecedor / 招标 关联查询
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.exceptions import HttpError
from src.fetchers import pncp_contratos
from src.fetchers.pncp_contratos import MIN_PAGE_SIZE, fetch_contratos, fetch_contratos_all
from src.storage import (
    Base,
    Contrato,
    ContratoIn,
    ContratoRaw,
    ContratoRepository,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "pncp_contratos_sample.json"
)


# ─── Fake BrowserSession ────────────────────────────────────────────────


class FakeBrowserSession:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses: deque[Any] = deque(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FakeBrowserSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        parsed_qs = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        merged = {**parsed_qs, **(params or {})}
        self.calls.append({"url": url, "params": merged})
        if not self._responses:
            raise AssertionError("no more queued responses")
        nxt = self._responses.popleft()
        if isinstance(nxt, HttpError):
            raise nxt
        return nxt


@pytest.fixture
def real_envelope() -> dict:
    """真实抓的 10 条合同样本(P3 探测时落地的)。"""
    if not FIXTURE_PATH.exists():
        pytest.skip(f"fixture missing: {FIXTURE_PATH}")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def patch_session(monkeypatch: pytest.MonkeyPatch):
    def _make(responses: list[Any]) -> FakeBrowserSession:
        session = FakeBrowserSession(responses=responses)

        def _factory(*args: Any, **kwargs: Any) -> FakeBrowserSession:
            return session

        monkeypatch.setattr(pncp_contratos, "PncpSession", _factory)
        return session

    return _make


# ─── fetcher 行为 ──────────────────────────────────────────────────────


async def test_fetch_contratos_does_not_send_modalidade(tmp_path: Path) -> None:
    """合同接口不需要 modalidade — query 里不能出现。"""
    session = FakeBrowserSession(
        responses=[{"data": [], "totalRegistros": 0, "totalPaginas": 0}]
    )
    await fetch_contratos(
        session, "2026-05-20", "2026-05-27", page=1, page_size=10, cache_root=tmp_path
    )
    p = session.calls[0]["params"]
    assert "codigoModalidadeContratacao" not in p
    assert p["dataInicial"] == "20260520"
    assert p["dataFinal"] == "20260527"
    assert p["pagina"] == 1
    assert p["tamanhoPagina"] == 10


async def test_fetch_contratos_page_size_clamped_to_min(tmp_path: Path) -> None:
    """page_size < 10 要被夹到 10。"""
    session = FakeBrowserSession(
        responses=[{"data": [], "totalRegistros": 0, "totalPaginas": 0}]
    )
    await fetch_contratos(
        session, "2026-05-20", "2026-05-27", page=1, page_size=3, cache_root=tmp_path
    )
    assert session.calls[0]["params"]["tamanhoPagina"] == MIN_PAGE_SIZE


async def test_fetch_contratos_cache_path_no_modalidade_layer(
    real_envelope: dict, tmp_path: Path
) -> None:
    """缓存路径:contratos/YYYY-MM-DD/page_N.json(没 modalidade 子层)。"""
    session = FakeBrowserSession(responses=[real_envelope])
    await fetch_contratos(
        session, "2026-05-20", "2026-05-27", page=1, page_size=10, cache_root=tmp_path
    )
    expected = tmp_path / "contratos" / "2026-05-20" / "page_1.json"
    assert expected.exists()


async def test_fetch_contratos_404_returns_none(tmp_path: Path) -> None:
    session = FakeBrowserSession(
        responses=[HttpError("HTTP 404", status_code=404, url="...")]
    )
    result = await fetch_contratos(
        session, "2026-05-20", "2026-05-27", page=99, cache_root=tmp_path
    )
    assert result is None


async def test_fetch_contratos_all_iterates_pages(
    real_envelope: dict, tmp_path: Path, patch_session
) -> None:
    """跨页遍历;contratos 无 modalidade 循环。"""
    page1 = {**real_envelope, "totalPaginas": 2, "numeroPagina": 1}
    page2 = {**real_envelope, "totalPaginas": 2, "numeroPagina": 2}
    patch_session([page1, page2])

    records = []
    async for rec in fetch_contratos_all(
        "2026-05-20", "2026-05-27", page_size=10, cache_root=tmp_path
    ):
        records.append(rec)

    # 10 条 × 2 页 = 20
    assert len(records) == 20


# ─── schema 校验 ───────────────────────────────────────────────────────


def test_contrato_raw_parses_real_sample(real_envelope: dict) -> None:
    """真实 10 条合同全部能被 ContratoRaw 校验。"""
    records = real_envelope["data"]
    assert len(records) == 10
    for r in records:
        parsed = ContratoRaw.model_validate(r)
        assert parsed.numeroControlePNCP
        # 合同自己的 ID 第二段是 2(tipo=2)
        assert "-2-" in parsed.numeroControlePNCP


def test_to_contrato_in_flattens_nested(real_envelope: dict) -> None:
    """嵌套对象(tipoContrato / categoriaProcesso / orgaoEntidade / unidadeOrgao)
    正确扁平化到 snake_case。"""
    r = real_envelope["data"][0]
    raw = ContratoRaw.model_validate(r)
    ci = raw.to_contrato_in(raw_json=r)

    assert ci.pncp_id == r["numeroControlePNCP"]
    assert ci.linked_contratacao_pncp_id == r["numeroControlePncpCompra"]
    assert ci.tipo_contrato_id == r["tipoContrato"]["id"]
    assert ci.tipo_contrato_nome == r["tipoContrato"]["nome"]
    assert ci.categoria_processo_id == r["categoriaProcesso"]["id"]
    assert ci.orgao_cnpj == r["orgaoEntidade"]["cnpj"]
    assert ci.orgao_razao_social == r["orgaoEntidade"]["razaoSocial"]
    assert ci.uf_sigla == r["unidadeOrgao"]["ufSigla"]
    assert ci.ni_fornecedor == r["niFornecedor"]
    assert ci.nome_razao_social_fornecedor == r["nomeRazaoSocialFornecedor"]
    assert ci.valor_global == r["valorGlobal"]
    assert ci.raw_json == r


# ─── ContratoRepository ────────────────────────────────────────────────


@pytest.fixture
def memory_engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


def test_contrato_repository_upsert_idempotent(memory_engine) -> None:
    """同 pncp_id 调两次 → 1 行,字段被覆盖。"""
    with Session(memory_engine) as session:
        repo = ContratoRepository(session)
        repo.upsert(
            ContratoIn(
                pncp_id="x-2-001/2026",
                nome_razao_social_fornecedor="供应商 V1",
                valor_global=100.0,
            )
        )
        repo.upsert(
            ContratoIn(
                pncp_id="x-2-001/2026",
                nome_razao_social_fornecedor="供应商 V2",
                valor_global=200.0,
            )
        )
        session.commit()
        assert repo.count() == 1
        rec = repo.get("x-2-001/2026")
        assert rec.nome_razao_social_fornecedor == "供应商 V2"
        assert rec.valor_global == 200.0


def test_contrato_repository_upsert_real_10_records(
    memory_engine, real_envelope: dict
) -> None:
    """真实 10 条合同走完整链路:Raw → ContratoIn → UPSERT。"""
    with Session(memory_engine) as session:
        repo = ContratoRepository(session)
        for r in real_envelope["data"]:
            raw = ContratoRaw.model_validate(r)
            repo.upsert(raw.to_contrato_in(raw_json=r))
        session.commit()
        assert repo.count() == 10


def test_contrato_repository_list_by_fornecedor(memory_engine) -> None:
    """按供应商 CNPJ 查最近合同(竞品分析常用)。"""
    with Session(memory_engine) as session:
        repo = ContratoRepository(session)
        repo.upsert(
            ContratoIn(pncp_id="a-2-001/2026", ni_fornecedor="11111111000111")
        )
        repo.upsert(
            ContratoIn(pncp_id="b-2-002/2026", ni_fornecedor="11111111000111")
        )
        repo.upsert(
            ContratoIn(pncp_id="c-2-003/2026", ni_fornecedor="22222222000122")
        )
        session.commit()
        rs = repo.list_by_fornecedor("11111111000111")
        assert len(rs) == 2


def test_contrato_repository_list_by_contratacao(memory_engine) -> None:
    """按招标 pncp_id 查产生的所有合同(分包场景)。"""
    with Session(memory_engine) as session:
        repo = ContratoRepository(session)
        repo.upsert(
            ContratoIn(
                pncp_id="c1-2-001/2026",
                linked_contratacao_pncp_id="parent-1-001/2025",
            )
        )
        repo.upsert(
            ContratoIn(
                pncp_id="c2-2-002/2026",
                linked_contratacao_pncp_id="parent-1-001/2025",
            )
        )
        repo.upsert(
            ContratoIn(
                pncp_id="c3-2-003/2026",
                linked_contratacao_pncp_id="other-1-005/2025",
            )
        )
        session.commit()
        rs = repo.list_by_contratacao("parent-1-001/2025")
        assert len(rs) == 2
