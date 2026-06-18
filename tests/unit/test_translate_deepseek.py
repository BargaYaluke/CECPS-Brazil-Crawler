"""tests/unit/test_translate_deepseek.py — DeepSeek 批量翻译 + run_translate + 缓存。

deepseek_translate_batch 用 pytest_httpx mock /chat/completions;run_translate 用
内存库 + monkeypatch get_engine(沿用既有手法)。验证:JSON 数组解析、长度校验、
无 key 回退、缓存去重、导出读缓存。
"""
from __future__ import annotations

import json
import re

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.enrichers.translate import deepseek_translate_batch, text_hash, translate_texts
from src.pipeline import orchestrator as orch
from src.storage import Base, Contratacao, ContratacaoIn, ContratacaoRepository, TranslationRepository

DS_URL = re.compile(r"https://api\.deepseek\.com/chat/completions")


def _ds_response(translations: list[str]) -> dict:
    """构造 DeepSeek(OpenAI 兼容)返回:content 是 JSON 数组字符串。"""
    return {"choices": [{"message": {"content": json.dumps(translations, ensure_ascii=False)}}]}


@pytest.fixture
def translation_cfg(monkeypatch: pytest.MonkeyPatch):
    """给翻译配置塞一个假 key,避免依赖真实 .env。"""
    monkeypatch.setattr(
        "src.enrichers.translate.get_translation_settings",
        lambda *a, **k: {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
            "max_chars": 500,
            "timeout": 60,
            "batch_size": 20,
        },
    )


@pytest.fixture
def isolated_engine(monkeypatch: pytest.MonkeyPatch):
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    monkeypatch.setattr("src.storage.database.get_engine", lambda *a, **kw: eng)
    monkeypatch.setattr("src.pipeline.orchestrator.init_db", lambda: None)
    yield eng
    eng.dispose()


# ─── deepseek_translate_batch ──────────────────────────────────────────


async def test_batch_translate_ok(translation_cfg, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=DS_URL, json=_ds_response(["采购药品", "工程施工"]))
    out = await deepseek_translate_batch(["Aquisição de medicamentos", "Execução de obras"])
    assert out == ["采购药品", "工程施工"]


async def test_batch_translate_strips_code_fence(translation_cfg, httpx_mock: HTTPXMock) -> None:
    fenced = {"choices": [{"message": {"content": '```json\n["采购药品"]\n```'}}]}
    httpx_mock.add_response(url=DS_URL, json=fenced)
    out = await deepseek_translate_batch(["Aquisição de medicamentos"])
    assert out == ["采购药品"]


async def test_batch_translate_length_mismatch_returns_none(translation_cfg, httpx_mock: HTTPXMock) -> None:
    """返回长度不符 → None(调用方回退离线)。"""
    httpx_mock.add_response(url=DS_URL, json=_ds_response(["只翻了一条"]))
    out = await deepseek_translate_batch(["texto 1", "texto 2"])
    assert out is None


async def test_batch_translate_no_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.enrichers.translate.get_translation_settings",
        lambda *a, **k: {"base_url": "x", "model": "m", "api_key": "", "max_chars": 500, "timeout": 60},
    )
    out = await deepseek_translate_batch(["texto"])
    assert out is None


# ─── translate_texts(二分递归兜底)──────────────────────────────────


async def test_translate_texts_bisects_on_failure(translation_cfg, httpx_mock: HTTPXMock) -> None:
    """整批(2 条)长度不符失败 → 二分成 2 个单条,各自成功。"""
    calls = {"n": 0}

    def _resp(request):
        import httpx as _h

        calls["n"] += 1
        body = json.loads(request.content)
        n = len([ln for ln in body["messages"][1]["content"].splitlines() if ln.strip()])
        if n == 2:  # 整批:故意返回长度不符 → 触发二分
            return _h.Response(200, json=_ds_response(["only one"]))
        return _h.Response(200, json=_ds_response(["译" * 1 for _ in range(n)]))

    httpx_mock.add_callback(_resp, url=DS_URL, is_reusable=True)
    zhs, models = await translate_texts(["texto A", "texto B"])
    assert len(zhs) == 2
    assert models == ["deepseek-v4-flash", "deepseek-v4-flash"]  # 二分后两条都走 API
    assert calls["n"] == 3  # 1 次整批失败 + 2 次单条成功


# ─── run_translate ─────────────────────────────────────────────────────


async def test_run_translate_fills_cache_and_dedupes(
    isolated_engine, translation_cfg, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    # 3 条招标,其中 2 条 objeto 相同 → 去重后只 2 个不同文本
    with Session(isolated_engine) as s:
        repo = ContratacaoRepository(s)
        repo.upsert(ContratacaoIn(pncp_id="a/2026", objeto_compra="Aquisição de medicamentos"))
        repo.upsert(ContratacaoIn(pncp_id="b/2026", objeto_compra="Aquisição de medicamentos"))
        repo.upsert(ContratacaoIn(pncp_id="c/2026", objeto_compra="Execução de obras"))
        s.commit()

    # batch_size=20 → 一批 2 条;按 todo 顺序返回
    def _resp(request):
        import httpx as _h

        body = json.loads(request.content)
        user = body["messages"][1]["content"]
        n = len([ln for ln in user.splitlines() if ln.strip()])
        return _h.Response(200, json=_ds_response([f"译文{i}" for i in range(n)]))

    httpx_mock.add_callback(_resp, url=DS_URL, is_reusable=True)

    counters = await orch.run_translate()
    assert counters["distinct"] == 2  # 去重后 2 个不同文本
    assert counters["to_translate"] == 2
    assert counters["translated_api"] == 2
    assert counters["failed_batches"] == 0

    with Session(isolated_engine) as s:
        assert TranslationRepository(s).count() == 2

    # 重跑:全部已缓存 → 不再翻
    counters2 = await orch.run_translate()
    assert counters2["to_translate"] == 0


async def test_run_translate_no_key_falls_back_offline(
    isolated_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 key → 整批回退离线词典,仍写缓存(model=offline)。"""
    no_key = {"model": "deepseek-v4-flash", "api_key": "", "batch_size": 20, "max_chars": 500, "timeout": 60}
    # 两个模块各自 import 了 get_translation_settings:orchestrator(算 bs/has_key)+ translate(translate_texts 用)
    monkeypatch.setattr("src.pipeline.orchestrator.get_translation_settings", lambda *a, **k: no_key)
    monkeypatch.setattr("src.enrichers.translate.get_translation_settings", lambda *a, **k: no_key)
    with Session(isolated_engine) as s:
        ContratacaoRepository(s).upsert(
            ContratacaoIn(pncp_id="a/2026", objeto_compra="Aquisição de medicamentos")
        )
        s.commit()

    counters = await orch.run_translate()
    assert counters["translated_offline"] == 1
    assert counters["translated_api"] == 0
    with Session(isolated_engine) as s:
        m = TranslationRepository(s).load_map()
        assert m[text_hash("Aquisição de medicamentos")]  # 有离线译文
