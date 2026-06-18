"""tests/unit/test_pncp_pca.py — PCA fetcher + storage 单测。

覆盖:
- fetcher 用普通 HttpClient(无 BrowserSession),query 参数对
- page_size 自动 ≥ 10
- 真实 fixture(1 条 PCA + 10 个嵌套 itens)能被 PcaRaw 校验
- explode_items 把 1 个 PCA 头拍扁成 N 个 PcaItemIn(头信息冗余)
- PcaRepository UPSERT 幂等(复合键)
- 按 PCA / 机构 查询
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.http_client import HttpClient
from src.fetchers.pncp_pca import MIN_PAGE_SIZE, fetch_pca, fetch_pca_all
from src.storage import (
    Base,
    PcaItem,
    PcaItemIn,
    PcaRaw,
    PcaRepository,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "pncp_pca_sample.json"
)

PCA_URL_RE = re.compile(r"https://pncp\.gov\.br/api/consulta/v1/pca/atualizacao.*")


@pytest.fixture
def real_envelope() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"fixture missing: {FIXTURE_PATH}")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ─── fetcher ───────────────────────────────────────────────────────────


async def test_fetch_pca_single_page(
    httpx_mock: HTTPXMock, real_envelope: dict, tmp_path: Path
) -> None:
    """单页拉取 + 缓存落地;query 参数用 dataInicio / dataFim(不是 dataInicial / dataFinal)。"""
    httpx_mock.add_response(url=PCA_URL_RE, json=real_envelope)

    async with HttpClient(rate_limit_per_second=0) as client:
        result = await fetch_pca(
            client,
            "2026-05-20",
            "2026-05-27",
            page=1,
            page_size=10,
            cache_root=tmp_path,
        )

    assert result == real_envelope
    req = httpx_mock.get_requests()[0]
    url_s = str(req.url)
    # 关键:PCA 的日期参数名是 dataInicio / dataFim,不是 dataInicial / dataFinal
    assert "dataInicio=20260520" in url_s
    assert "dataFim=20260527" in url_s
    assert "codigoModalidadeContratacao" not in url_s  # PCA 无 modalidade
    # 缓存
    assert (tmp_path / "pca" / "2026-05-20" / "page_1.json").exists()


async def test_fetch_pca_page_size_clamped_to_min(
    httpx_mock: HTTPXMock, real_envelope: dict, tmp_path: Path
) -> None:
    """page_size < 10 自动夹到 10。"""
    httpx_mock.add_response(url=PCA_URL_RE, json=real_envelope)

    async with HttpClient(rate_limit_per_second=0) as client:
        await fetch_pca(
            client, "2026-05-20", "2026-05-27", page=1, page_size=3, cache_root=tmp_path
        )

    url_s = str(httpx_mock.get_requests()[0].url)
    assert f"tamanhoPagina={MIN_PAGE_SIZE}" in url_s


async def test_fetch_pca_404_returns_none(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    httpx_mock.add_response(url=PCA_URL_RE, status_code=404)

    async with HttpClient(rate_limit_per_second=0) as client:
        result = await fetch_pca(
            client, "2026-05-20", "2026-05-27", page=999, cache_root=tmp_path
        )
    assert result is None


async def test_fetch_pca_all_iterates_pages(
    httpx_mock: HTTPXMock, real_envelope: dict, tmp_path: Path
) -> None:
    """跨页;PCA 没有 modalidade 循环。"""
    page1 = {**real_envelope, "totalPaginas": 2, "numeroPagina": 1}
    page2 = {**real_envelope, "totalPaginas": 2, "numeroPagina": 2}
    httpx_mock.add_response(url=PCA_URL_RE, json=page1)
    httpx_mock.add_response(url=PCA_URL_RE, json=page2)

    pcas: list[dict] = []
    async for r in fetch_pca_all(
        "2026-05-20", "2026-05-27", page_size=10, cache_root=tmp_path
    ):
        pcas.append(r)

    # 1 PCA × 2 页 = 2 头部
    assert len(pcas) == 2


# ─── schema ────────────────────────────────────────────────────────────


def test_pca_raw_parses_real_sample(real_envelope: dict) -> None:
    records = real_envelope["data"]
    assert len(records) >= 1
    for r in records:
        parsed = PcaRaw.model_validate(r)
        assert parsed.idPcaPncp
        assert len(parsed.itens) > 0


def test_pca_explode_items(real_envelope: dict) -> None:
    """1 个 PCA 头 + 10 个 items → explode 出 10 条 PcaItemIn,头信息每条都有。"""
    r = real_envelope["data"][0]
    pca = PcaRaw.model_validate(r)
    exploded = pca.explode_items(raw_json=r)

    assert len(exploded) == 10
    raw_items = r["itens"]
    for idx, ii in enumerate(exploded):
        # 头信息冗余正确
        assert ii.id_pca_pncp == pca.idPcaPncp
        assert ii.ano_pca == pca.anoPca
        assert ii.orgao_entidade_cnpj == pca.orgaoEntidadeCnpj
        # numero_item 与原数据一致
        assert ii.numero_item >= 1
        # raw_json 只留底自己那条 item(不再复制整条 PCA 头 → 避免平方级膨胀)
        assert ii.raw_json == raw_items[idx]
        assert "itens" not in (ii.raw_json or {})


# ─── PcaRepository ─────────────────────────────────────────────────────


@pytest.fixture
def memory_engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


def test_pca_repository_upsert_idempotent(memory_engine) -> None:
    """同 (id_pca_pncp, numero_item) 跑两次 → 1 行,字段覆盖。"""
    with Session(memory_engine) as session:
        repo = PcaRepository(session)
        repo.upsert(
            PcaItemIn(
                id_pca_pncp="x-0-001/2027", numero_item=1, valor_total=100.0
            )
        )
        repo.upsert(
            PcaItemIn(
                id_pca_pncp="x-0-001/2027", numero_item=1, valor_total=200.0
            )
        )
        session.commit()
        assert repo.count() == 1


def test_pca_repository_upsert_real_sample(memory_engine, real_envelope: dict) -> None:
    """真实 1 个 PCA × 10 items 走完整链路。"""
    with Session(memory_engine) as session:
        repo = PcaRepository(session)
        for r in real_envelope["data"]:
            pca = PcaRaw.model_validate(r)
            for ii in pca.explode_items(raw_json=r):
                repo.upsert(ii)
        session.commit()

        assert repo.count() == 10
        assert repo.count_distinct_pca() == 1

        # 按 PCA 查
        items = repo.list_by_pca(real_envelope["data"][0]["idPcaPncp"])
        assert len(items) == 10
        # 按 numero_item 排序
        assert [it.numero_item for it in items] == sorted(it.numero_item for it in items)


def test_pca_repository_list_by_orgao(memory_engine) -> None:
    """业务方"看某机构未来要买什么"的查询。"""
    with Session(memory_engine) as session:
        repo = PcaRepository(session)
        # 同 CNPJ 不同 ano
        repo.upsert(
            PcaItemIn(
                id_pca_pncp="pcaA-0-001/2027",
                numero_item=1,
                orgao_entidade_cnpj="11111111000111",
                ano_pca=2027,
            )
        )
        repo.upsert(
            PcaItemIn(
                id_pca_pncp="pcaB-0-002/2028",
                numero_item=1,
                orgao_entidade_cnpj="11111111000111",
                ano_pca=2028,
            )
        )
        # 不同 CNPJ
        repo.upsert(
            PcaItemIn(
                id_pca_pncp="pcaC-0-003/2027",
                numero_item=1,
                orgao_entidade_cnpj="22222222000122",
                ano_pca=2027,
            )
        )
        session.commit()

        # 不限年份:同机构 2 条
        assert len(repo.list_by_orgao("11111111000111")) == 2
        # 限 2027:1 条
        assert len(repo.list_by_orgao("11111111000111", ano=2027)) == 1
