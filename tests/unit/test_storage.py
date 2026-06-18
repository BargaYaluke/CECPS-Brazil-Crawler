"""tests/unit/test_storage.py — storage 层端到端单测。

不连真实数据库;每个测试用 ``sqlite:///:memory:`` 新建独立 engine。
最关键的测试 :func:`test_repository_upsert_50_real_records` 用刚才抓的 50 条
真实 PNCP 数据走完整链路(API JSON → pydantic → ORM → SQLite),
是 P1 ↔ P2 接口的活契约。
"""
from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from src.storage import (
    Base,
    Contratacao,
    ContratacaoIn,
    ContratacaoRepository,
    CursorRepository,
    ItemIn,
    ItemRaw,
    ItemRepository,
    PublicacaoRaw,
)

# 真实抓取产物,用于端到端测试;不存在时跳过
FIXTURE_50 = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "raw"
    / "publicacao"
    / "2026-05-26"
    / "modalidade_6_page_1.json"
)


@pytest.fixture
def memory_engine():
    """每个测试一个全新的内存数据库 engine。"""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


# ─── ORM 建表 ──────────────────────────────────────────────────────────


def test_models_create_all_tables(memory_engine) -> None:
    """create_all 后能看到 lite 版 4 张表(招标主表 + 明细 + 翻译缓存 + 游标)。"""
    insp = inspect(memory_engine)
    tables = set(insp.get_table_names())
    assert tables == {
        "contratacoes",
        "itens",
        "translation_cache",
        "sync_cursor",
    }


def test_contratacoes_has_indexes(memory_engine) -> None:
    """关键查询列(orgao_cnpj / uf_sigla / modalidade_id / data_publicacao_pncp)
    都建了索引。"""
    insp = inspect(memory_engine)
    index_cols = {
        col_name
        for idx in insp.get_indexes("contratacoes")
        for col_name in idx["column_names"]
    }
    assert "orgao_cnpj" in index_cols
    assert "uf_sigla" in index_cols
    assert "modalidade_id" in index_cols
    assert "data_publicacao_pncp" in index_cols


# ─── Pydantic schemas ──────────────────────────────────────────────────


def test_publicacao_raw_parses_minimal_record() -> None:
    raw = PublicacaoRaw.model_validate(
        {"numeroControlePNCP": "abc-1-001/2026", "modalidadeNome": "Pregão"}
    )
    assert raw.numeroControlePNCP == "abc-1-001/2026"
    assert raw.modalidadeNome == "Pregão"
    assert raw.amparoLegal is None


def test_publicacao_raw_allows_unknown_fields() -> None:
    """API 未来加字段不会让 fetcher 崩(extra='allow')。"""
    raw = PublicacaoRaw.model_validate(
        {"numeroControlePNCP": "x-1-1/2026", "futureField": "whatever"}
    )
    assert raw.numeroControlePNCP == "x-1-1/2026"


def test_publicacao_raw_parses_iso_datetime() -> None:
    raw = PublicacaoRaw.model_validate(
        {
            "numeroControlePNCP": "x-1-1/2026",
            "dataPublicacaoPncp": "2026-05-26T10:30:00",
        }
    )
    assert raw.dataPublicacaoPncp == datetime(2026, 5, 26, 10, 30, 0)


def test_to_contratacao_in_flattens_nested() -> None:
    """嵌套对象正确扁平化到 snake_case。"""
    raw_data = {
        "numeroControlePNCP": "xxx-1-001/2026",
        "objetoCompra": "test",
        "anoCompra": 2026,
        "sequencialCompra": 1,
        "amparoLegal": {"codigo": 1, "nome": "Lei 14.133/2021"},
        "orgaoEntidade": {
            "cnpj": "11111111000111",
            "razaoSocial": "TEST ORG",
            "esferaId": "F",
            "poderId": "E",
        },
        "unidadeOrgao": {
            "ufSigla": "DF",
            "ufNome": "Distrito Federal",
            "municipioNome": "Brasília",
            "codigoIbge": "5300108",
        },
    }
    raw = PublicacaoRaw.model_validate(raw_data)
    ci = raw.to_contratacao_in(raw_json=raw_data)

    assert ci.pncp_id == "xxx-1-001/2026"
    assert ci.objeto_compra == "test"
    assert ci.ano_compra == 2026
    assert ci.amparo_legal_codigo == 1
    assert ci.amparo_legal_nome == "Lei 14.133/2021"
    assert ci.orgao_cnpj == "11111111000111"
    assert ci.orgao_esfera_id == "F"
    assert ci.orgao_poder_id == "E"
    assert ci.uf_sigla == "DF"
    assert ci.municipio_nome == "Brasília"
    assert ci.raw_json == raw_data


def test_to_contratacao_in_handles_missing_nested() -> None:
    """缺少 amparoLegal / orgaoEntidade / unidadeOrgao 时,对应字段为 None。"""
    raw = PublicacaoRaw.model_validate({"numeroControlePNCP": "minimal/2026"})
    ci = raw.to_contratacao_in()
    assert ci.pncp_id == "minimal/2026"
    assert ci.amparo_legal_codigo is None
    assert ci.orgao_cnpj is None
    assert ci.uf_sigla is None


# ─── UPSERT 幂等 ────────────────────────────────────────────────────────


def test_repository_upsert_inserts_then_updates(memory_engine) -> None:
    """同一 pncp_id 调两次 → 只剩 1 行;字段被新值覆盖。"""
    with Session(memory_engine) as session:
        repo = ContratacaoRepository(session)
        repo.upsert(
            ContratacaoIn(pncp_id="dup-1-001/2026", objeto_compra="v1", valor_total_estimado=100.0)
        )
        repo.upsert(
            ContratacaoIn(pncp_id="dup-1-001/2026", objeto_compra="v2", valor_total_estimado=200.0)
        )
        session.commit()

        assert repo.count() == 1
        rec = repo.get("dup-1-001/2026")
        assert rec is not None
        assert rec.objeto_compra == "v2"
        assert rec.valor_total_estimado == 200.0


def test_repository_upsert_distinct_ids_creates_distinct_rows(memory_engine) -> None:
    with Session(memory_engine) as session:
        repo = ContratacaoRepository(session)
        repo.upsert(ContratacaoIn(pncp_id="a/2026"))
        repo.upsert(ContratacaoIn(pncp_id="b/2026"))
        repo.upsert(ContratacaoIn(pncp_id="c/2026"))
        session.commit()
        assert repo.count() == 3


def test_repository_upsert_batch(memory_engine) -> None:
    with Session(memory_engine) as session:
        repo = ContratacaoRepository(session)
        items = [ContratacaoIn(pncp_id=f"batch-{i}/2026") for i in range(20)]
        n = repo.upsert_batch(items)
        session.commit()
        assert n == 20
        assert repo.count() == 20


# ─── 真实数据端到端 ────────────────────────────────────────────────────


@pytest.mark.skipif(not FIXTURE_50.exists(), reason=f"需要 fetch-publicacao 先抓数据到 {FIXTURE_50}")
def test_repository_upsert_50_real_records(memory_engine) -> None:
    """用刚才真实抓到的 50 条 PNCP 数据走完整链路。

    这是 P1 fetcher 输出 → P2 storage 入库的活契约。
    任何一端字段对不上,此测试就会失败。
    """
    envelope = json.loads(FIXTURE_50.read_text(encoding="utf-8"))
    records = envelope["data"]
    assert len(records) == 50

    with Session(memory_engine) as session:
        repo = ContratacaoRepository(session)
        for r in records:
            raw = PublicacaoRaw.model_validate(r)
            repo.upsert(raw.to_contratacao_in(raw_json=r))
        session.commit()

        assert repo.count() == 50

        # 抽查第 1 条 — PR 州 Bela Vista do Caroba
        first = repo.get("01612441000107-1-000076/2026")
        assert first is not None
        assert first.modalidade_id == 6
        assert first.modalidade_nome == "Pregão - Eletrônico"
        assert first.uf_sigla == "PR"
        assert first.amparo_legal_codigo == 1
        assert first.orgao_esfera_id == "M"
        assert first.orgao_poder_id == "N"
        assert first.srp is True
        assert first.valor_total_estimado == 95824.54
        assert first.raw_json is not None
        assert first.raw_json["numeroControlePNCP"] == first.pncp_id

        # 抽查最后一条
        last = repo.get(records[-1]["numeroControlePNCP"])
        assert last is not None
        assert last.modalidade_id == 6

        # 验证按 modalidade 查询能拿到所有 50 条
        results = repo.list_by_modalidade(6, limit=100)
        assert len(results) == 50


@pytest.mark.skipif(not FIXTURE_50.exists(), reason=f"需要 fetch-publicacao 先抓数据到 {FIXTURE_50}")
def test_real_data_re_upsert_is_idempotent(memory_engine) -> None:
    """跑两次 50 条入库 → 仍然只有 50 条。"""
    envelope = json.loads(FIXTURE_50.read_text(encoding="utf-8"))
    records = envelope["data"]

    with Session(memory_engine) as session:
        repo = ContratacaoRepository(session)
        for _ in range(2):
            for r in records:
                raw = PublicacaoRaw.model_validate(r)
                repo.upsert(raw.to_contratacao_in(raw_json=r))
            session.commit()
        assert repo.count() == 50


# ─── 招标明细 ──────────────────────────────────────────────────────────


def test_item_raw_parses_real_sample() -> None:
    """真实 itens API 样本(36 字段)能被 ItemRaw 校验通过。"""
    real_item = {
        "numeroItem": 1,
        "descricao": "SERVIÇO DE HORA TECNICA",
        "materialOuServico": "S",
        "materialOuServicoNome": "Serviço",
        "valorUnitarioEstimado": 278.2385,
        "valorTotal": 278.24,
        "quantidade": 1.0,
        "unidadeMedida": "HORAS",
        "tipoBeneficio": 5,
        "tipoBeneficioNome": "Não se aplica",
        "incentivoProdutivoBasico": False,
        "exigenciaConteudoNacional": False,
    }
    raw = ItemRaw.model_validate(real_item)
    assert raw.numeroItem == 1
    assert raw.materialOuServico == "S"

    ii = raw.to_item_in(pncp_id="abc-1-001/2026", raw_json=real_item)
    assert ii.pncp_id == "abc-1-001/2026"
    assert ii.numero_item == 1
    assert ii.material_ou_servico == "S"
    assert ii.material_ou_servico_nome == "Serviço"
    assert ii.incentivo_produtivo_basico is False
    assert ii.raw_json == real_item


def test_item_repository_upsert_idempotent(memory_engine) -> None:
    """同 (pncp_id, numero_item) 调两次 → 1 行,字段更新。"""
    # 先建一条 contratacoes(itens 外键依赖)
    with Session(memory_engine) as session:
        c_repo = ContratacaoRepository(session)
        c_repo.upsert(ContratacaoIn(pncp_id="parent-1-001/2026"))
        session.commit()

    with Session(memory_engine) as session:
        i_repo = ItemRepository(session)
        i_repo.upsert(
            ItemIn(pncp_id="parent-1-001/2026", numero_item=1, descricao="v1", valor_total=10.0)
        )
        i_repo.upsert(
            ItemIn(pncp_id="parent-1-001/2026", numero_item=1, descricao="v2", valor_total=20.0)
        )
        session.commit()

        assert i_repo.count() == 1
        items = i_repo.list_by_pncp_id("parent-1-001/2026")
        assert len(items) == 1
        assert items[0].descricao == "v2"
        assert items[0].valor_total == 20.0


def test_item_repository_multiple_items_per_contratacao(memory_engine) -> None:
    """同一 pncp_id 下多个 numero_item 都能入。"""
    with Session(memory_engine) as session:
        c_repo = ContratacaoRepository(session)
        c_repo.upsert(ContratacaoIn(pncp_id="multi-1-001/2026"))
        session.commit()

    with Session(memory_engine) as session:
        i_repo = ItemRepository(session)
        for n in range(1, 6):
            i_repo.upsert(ItemIn(pncp_id="multi-1-001/2026", numero_item=n))
        session.commit()

        assert i_repo.count_by_pncp_id("multi-1-001/2026") == 5


# ─── 增量游标 ──────────────────────────────────────────────────────────


def test_cursor_first_get_returns_none(memory_engine) -> None:
    with Session(memory_engine) as session:
        cursor_repo = CursorRepository(session)
        assert cursor_repo.get("publicacao") is None


def test_cursor_update_then_get(memory_engine) -> None:
    with Session(memory_engine) as session:
        cursor_repo = CursorRepository(session)
        cursor_repo.update("publicacao", Date(2026, 5, 26), Date(2026, 5, 26))
        session.commit()

        c = cursor_repo.get("publicacao")
        assert c is not None
        assert c.fetcher_name == "publicacao"
        assert c.last_data_inicial == Date(2026, 5, 26)
        assert c.last_data_final == Date(2026, 5, 26)
        assert c.last_run_at is not None


def test_cursor_update_overwrites(memory_engine) -> None:
    """同一 fetcher 第二次 update 覆盖,不创建新行。"""
    with Session(memory_engine) as session:
        cursor_repo = CursorRepository(session)
        cursor_repo.update("publicacao", Date(2026, 5, 20), Date(2026, 5, 24))
        cursor_repo.update("publicacao", Date(2026, 5, 25), Date(2026, 5, 27))
        session.commit()

        c = cursor_repo.get("publicacao")
        assert c is not None
        assert c.last_data_inicial == Date(2026, 5, 25)
        assert c.last_data_final == Date(2026, 5, 27)
