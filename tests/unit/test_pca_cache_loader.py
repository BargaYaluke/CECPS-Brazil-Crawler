"""tests/unit/test_pca_cache_loader.py — PCA 缓存救回 + run_pca 逐页提交。

覆盖:
- load_pca_from_cache 读 data/raw/pca/**/page_*.json → 入库(不联网)
- 多文件 + commit_every 逐批提交
- limit 截断
- 坏文件跳过不崩
- run_pca 逐页提交(中断后已提交批次仍在库)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.pipeline import orchestrator as orch
from src.storage import Base, PcaItem

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "pncp_pca_sample.json"
)


@pytest.fixture
def real_envelope() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"fixture missing: {FIXTURE_PATH}")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def isolated_engine(monkeypatch: pytest.MonkeyPatch):
    """让 orchestrator 的 session_scope()/init_db() 落到内存库。"""
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    monkeypatch.setattr("src.storage.database.get_engine", lambda *a, **kw: eng)
    monkeypatch.setattr("src.pipeline.orchestrator.init_db", lambda: None)
    yield eng
    eng.dispose()


def _write_cache_pages(cache_root: Path, envelope: dict, n_pages: int) -> None:
    """在 cache_root/pca/2026-05-22/ 下造 n_pages 个 page JSON(每个 = 1 个 fixture envelope)。"""
    d = cache_root / "pca" / "2026-05-22"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n_pages + 1):
        (d / f"page_{i}.json").write_text(
            json.dumps(envelope, ensure_ascii=False), encoding="utf-8"
        )


def test_load_pca_from_cache_basic(isolated_engine, real_envelope, tmp_path: Path) -> None:
    """3 个缓存页 × (1 PCA 头 × 10 itens) → 30 行入库。"""
    _write_cache_pages(tmp_path, real_envelope, n_pages=3)

    counters = orch.load_pca_from_cache(cache_root=tmp_path, commit_every=2)

    assert counters["files"] == 3
    assert counters["pcas"] == 3  # 每页 1 个 PCA 头
    assert counters["items"] == 30  # 3 × 10 itens
    assert counters["pcas_failed"] == 0

    with Session(isolated_engine) as s:
        assert s.query(PcaItem).count() == 10  # 同一 fixture → 同主键,UPSERT 后 10 条唯一


def test_load_pca_from_cache_limit(isolated_engine, real_envelope, tmp_path: Path) -> None:
    """limit 截断 PCA 头数。"""
    _write_cache_pages(tmp_path, real_envelope, n_pages=5)
    counters = orch.load_pca_from_cache(cache_root=tmp_path, limit=2)
    assert counters["pcas"] == 2  # 只处理 2 个头就停


def test_load_pca_from_cache_skips_bad_file(isolated_engine, real_envelope, tmp_path: Path) -> None:
    """坏 JSON 文件跳过,不影响好文件入库。"""
    _write_cache_pages(tmp_path, real_envelope, n_pages=1)
    bad = tmp_path / "pca" / "2026-05-22" / "page_999.json"
    bad.write_text("{ not valid json", encoding="utf-8")

    counters = orch.load_pca_from_cache(cache_root=tmp_path)
    assert counters["files"] == 2  # 找到 2 个文件
    assert counters["pcas"] == 1  # 只有好文件的 1 个头入库


def test_load_pca_from_cache_empty_dir(isolated_engine, tmp_path: Path) -> None:
    """没有 pca 缓存目录 → 0,不崩。"""
    counters = orch.load_pca_from_cache(cache_root=tmp_path)
    assert counters == {"files": 0, "pcas": 0, "items": 0, "pcas_failed": 0}


async def test_run_pca_commits_incrementally(
    isolated_engine, real_envelope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_pca 逐页提交:即使中途"中断"(模拟),已提交批次仍在库。"""
    heads = real_envelope["data"]  # 1 个头

    # mock fetch_pca_all 连续 yield 5 个头(复用同一 fixture 头,但改 id 让主键不同)
    async def fake_fetch_all(*args, **kwargs):
        for i in range(5):
            h = dict(heads[0])
            h["idPcaPncp"] = f"PCA-{i}/2027"
            yield h

    monkeypatch.setattr(orch, "fetch_pca_all", fake_fetch_all)

    # commit_every=2 → 5 个头会提交 2+2,最后尾巴 1 个
    counters = await orch.run_pca(
        "2026-05-22", "2026-05-28", page_size=10, commit_every=2
    )
    assert counters["pcas"] == 5
    assert counters["items"] == 50  # 5 头 × 10 itens

    with Session(isolated_engine) as s:
        # 5 个不同 id × 10 itens = 50 行
        assert s.query(PcaItem).count() == 50
