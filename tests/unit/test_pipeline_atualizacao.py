"""tests/unit/test_pipeline_atualizacao.py — 增量编排的 cursor 行为测试。

不真启动 Chromium 也不打真 PNCP。Monkeypatch fetcher 和 BrowserSession,
验证 :func:`run_atualizacao_incremental` 的"读 cursor / 算日期 / 写 cursor"
逻辑在 3 种模式(默认增量 / --full / 显式日期)下都正确。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.utils import parse_pncp_id  # noqa: F401  (确保 import 不报错)
from src.pipeline import orchestrator as orch
from src.storage import Base, CursorRepository


@pytest.fixture
def isolated_engine(monkeypatch: pytest.MonkeyPatch):
    """每个测试都用全新的内存 DB,确保 cursor 测试互相隔离。"""
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)

    # storage.get_engine() 走 lru_cache;monkeypatch 一下,让 session_scope 拿到内存 eng
    monkeypatch.setattr("src.storage.database.get_engine", lambda *a, **kw: eng)
    monkeypatch.setattr("src.pipeline.orchestrator.init_db", lambda: None)
    yield eng
    eng.dispose()


@pytest.fixture
def fake_pipeline(monkeypatch: pytest.MonkeyPatch):
    """把 _run_list_pipeline 替换成 noop,只检 cursor 行为。"""
    calls: list[dict[str, Any]] = []

    async def _fake(*, fetch_iter, limit, itens_concurrency, skip_itens=False):
        # fetch_iter 是 async generator;拉一下让它启动
        async for _ in fetch_iter:
            pass
        calls.append({"limit": limit, "skip_itens": skip_itens})
        return {
            "contratacoes_kept": 0,
            "contratacoes_filtered": 0,
            "rule_hits": {},
            "tag_hits": {},
            "itens": 0,
            "itens_failed_pncp_ids": 0,
            "filter_mode": "hard_delete",
        }

    async def _fake_fetcher(*args, **kwargs):
        # async generator yielding nothing
        if False:
            yield  # pragma: no cover

    monkeypatch.setattr(orch, "_run_list_pipeline", _fake)
    monkeypatch.setattr(orch, "fetch_atualizacao_all", _fake_fetcher)
    return calls


# ─── 模式 1:显式日期 ──────────────────────────────────────────────────


async def test_explicit_dates_skip_cursor(isolated_engine, fake_pipeline) -> None:
    """显式 start/end → 不读不写 cursor。"""
    result = await orch.run_atualizacao_incremental(
        date_start="2026-05-01",
        date_end="2026-05-15",
    )
    assert result["mode_used"] == "explicit_range"
    assert result["date_start"] == "2026-05-01"
    assert result["date_end"] == "2026-05-15"

    # cursor 没被写
    with Session(isolated_engine) as session:
        assert CursorRepository(session).get(orch.ATUALIZACAO_FETCHER_NAME) is None


# ─── 模式 2:--full ────────────────────────────────────────────────────


async def test_force_full_uses_lookback_and_writes_cursor(
    isolated_engine, fake_pipeline
) -> None:
    """force_full=True → 跑 today-7d → today,**写** cursor。"""
    today = date.today()
    result = await orch.run_atualizacao_incremental(force_full=True)

    assert result["mode_used"] == "full"
    assert result["date_end"] == today.isoformat()
    assert result["date_start"] == (today - timedelta(days=7)).isoformat()

    with Session(isolated_engine) as session:
        c = CursorRepository(session).get(orch.ATUALIZACAO_FETCHER_NAME)
    assert c is not None
    assert c.last_data_final == today


# ─── 模式 3:默认增量(首次)──────────────────────────────────────────


async def test_incremental_first_run_falls_back_to_lookback(
    isolated_engine, fake_pipeline
) -> None:
    """没 cursor → 用 today-7d 兜底,写 cursor。"""
    today = date.today()
    result = await orch.run_atualizacao_incremental()

    assert result["mode_used"] == "incremental_first_run"
    assert result["date_end"] == today.isoformat()
    assert result["date_start"] == (today - timedelta(days=7)).isoformat()

    with Session(isolated_engine) as session:
        c = CursorRepository(session).get(orch.ATUALIZACAO_FETCHER_NAME)
    assert c is not None


# ─── 模式 4:默认增量(已有 cursor)────────────────────────────────────


async def test_incremental_uses_existing_cursor(isolated_engine, fake_pipeline) -> None:
    """已有 cursor → data_inicial = cursor.last_data_final,允许 1 天重叠。"""
    # 先预置 cursor
    prev_final = date(2026, 5, 20)
    with Session(isolated_engine) as session:
        CursorRepository(session).update(
            orch.ATUALIZACAO_FETCHER_NAME,
            date(2026, 5, 15),
            prev_final,
        )
        session.commit()

    today = date.today()
    result = await orch.run_atualizacao_incremental()

    assert result["mode_used"] == "incremental"
    assert result["date_start"] == prev_final.isoformat()  # = 上次 last_data_final
    assert result["date_end"] == today.isoformat()

    # cursor 被覆盖更新
    with Session(isolated_engine) as session:
        c = CursorRepository(session).get(orch.ATUALIZACAO_FETCHER_NAME)
    assert c is not None
    assert c.last_data_final == today
    assert c.last_data_inicial == prev_final
