"""tests/unit/test_purge_expired.py — run_purge_expired:删已过期招标 + 写删除日志。

内存 SQLite + monkeypatch 让 orchestrator 的 session_scope()/init_db() 落到内存库。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.pipeline import orchestrator as orch
from src.storage import Base, Contratacao, Item


@pytest.fixture
def memory_engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def isolated_engine(monkeypatch: pytest.MonkeyPatch, memory_engine):
    monkeypatch.setattr("src.storage.database.get_engine", lambda *a, **kw: memory_engine)
    monkeypatch.setattr("src.pipeline.orchestrator.init_db", lambda: None)
    return memory_engine


def test_purge_expired_deletes_only_past(isolated_engine, tmp_path: Path) -> None:
    with Session(isolated_engine) as s:
        # 已过期(昨天之前)→ 应删,连同明细
        s.add(Contratacao(pncp_id="exp-1/2026", objeto_compra="过期标",
                          valor_total_estimado=1000.0,
                          data_encerramento_proposta=datetime(2026, 1, 1)))
        s.add(Item(pncp_id="exp-1/2026", numero_item=1, descricao="x"))
        # 未来截止 → 保留
        s.add(Contratacao(pncp_id="open-1/2026", objeto_compra="未来标",
                          data_encerramento_proposta=datetime(2026, 12, 31)))
        # 2099 哨兵(无截止)→ 保留
        s.add(Contratacao(pncp_id="open-2099/2026", objeto_compra="无截止",
                          data_encerramento_proposta=datetime(2099, 12, 31)))
        # 截止日 NULL(时效未知)→ 保留
        s.add(Contratacao(pncp_id="null-1/2026", objeto_compra="无日期",
                          data_encerramento_proposta=None))
        s.commit()

    log = tmp_path / "expired_purged.csv"
    counters = orch.run_purge_expired(today=date(2026, 6, 22), log_path=log)

    assert counters["expired_found"] == 1
    assert counters["contratacoes_deleted"] == 1
    assert counters["itens_deleted"] == 1

    with Session(isolated_engine) as s:
        remaining = {pid for (pid,) in s.execute(select(Contratacao.pncp_id)).all()}
        items_left = s.execute(select(Item.pncp_id)).all()
    assert remaining == {"open-1/2026", "open-2099/2026", "null-1/2026"}
    assert items_left == []  # exp-1 的明细也被删

    # 删除日志写了被删标的信息
    assert log.exists()
    text = log.read_text(encoding="utf-8-sig")
    assert "exp-1/2026" in text and "过期标" in text
    assert "删除时间" in text  # 表头


def test_purge_expired_none_when_empty(isolated_engine, tmp_path: Path) -> None:
    log = tmp_path / "expired_purged.csv"
    counters = orch.run_purge_expired(today=date(2026, 6, 22), log_path=log)
    assert counters["expired_found"] == 0
    assert counters["contratacoes_deleted"] == 0
    assert not log.exists()  # 没有过期标就不建日志
