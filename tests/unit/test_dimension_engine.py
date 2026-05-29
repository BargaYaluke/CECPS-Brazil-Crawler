"""tests/unit/test_dimension_engine.py — 六大维度分类引擎单测。

覆盖:
- 真实 yaml 加载(6 个维度)
- 各维度单命中(用葡语真实表述)
- 多维度命中 → primary 取最高分 + secondary
- 无命中 → primary=None
- confidence 计算 + low_confidence 标记
- 大小写 / 重音不敏感(复用 filter 的 normalize)
"""
from __future__ import annotations

import pytest

from src.classifiers.dimension_engine import (
    DimensionEngine,
    get_default_dimension_engine,
    load_dimension_rules,
)


@pytest.fixture(scope="module")
def engine() -> DimensionEngine:
    return DimensionEngine(load_dimension_rules())


# ─── yaml 加载 ─────────────────────────────────────────────────────────


def test_loads_six_dimensions(engine: DimensionEngine) -> None:
    dims = set(engine.rules.dimensions.keys())
    assert dims == {
        "digital_economy",
        "healthcare",
        "high_end_manufacturing",
        "commodities",
        "cross_border_ecommerce",
        "culture_sports",
    }
    # 每个维度都有关键词
    for dim, ddef in engine.rules.dimensions.items():
        assert len(ddef.keywords_pt) > 0, f"{dim} 没有葡语关键词"


# ─── 各维度单命中 ──────────────────────────────────────────────────────


def test_digital_economy(engine: DimensionEngine) -> None:
    r = engine.classify("AQUISIÇÃO DE LICENÇA DE SOFTWARE E SERVIDOR")
    assert r.primary == "digital_economy"
    assert r.confidence > 0


def test_healthcare(engine: DimensionEngine) -> None:
    r = engine.classify("Aquisição de medicamentos e material hospitalar para o hospital")
    assert r.primary == "healthcare"


def test_high_end_manufacturing(engine: DimensionEngine) -> None:
    r = engine.classify("Contratação de manutenção de máquina e equipamento industrial")
    assert r.primary == "high_end_manufacturing"


def test_commodities(engine: DimensionEngine) -> None:
    r = engine.classify("Aquisição de óleo diesel e combustível para frota")
    assert r.primary == "commodities"


def test_cross_border(engine: DimensionEngine) -> None:
    r = engine.classify("Serviço de despacho aduaneiro e logística internacional para importação")
    assert r.primary == "cross_border_ecommerce"


def test_culture_sports(engine: DimensionEngine) -> None:
    r = engine.classify("Contratação de evento esportivo e material esportivo para o ginásio")
    assert r.primary == "culture_sports"


# ─── 无命中 ────────────────────────────────────────────────────────────


def test_no_match_returns_none(engine: DimensionEngine) -> None:
    r = engine.classify("xyzabc nada relevante aqui 12345")
    assert r.primary is None
    assert r.confidence == 0.0
    assert r.secondary == []


def test_empty_text(engine: DimensionEngine) -> None:
    assert engine.classify("").primary is None
    assert engine.classify(None).primary is None


# ─── 多维度 + 优先级 + secondary ───────────────────────────────────────


def test_multi_dimension_primary_is_highest(engine: DimensionEngine) -> None:
    """同时提到医疗(3 词)+ 数字(1 词)→ primary=healthcare,数字进 secondary。"""
    text = (
        "Aquisição de equipamento medico, material hospitalar e medicamento, "
        "incluindo um software de gestão"
    )
    r = engine.classify(text)
    assert r.primary == "healthcare"
    # software 命中 digital_economy
    assert "digital_economy" in (r.secondary or []) or r.confidence < 1.0


def test_confidence_single_dimension_is_one(engine: DimensionEngine) -> None:
    """只命中一个维度 → confidence = 1.0(独占全部得分)。"""
    r = engine.classify("software software software")  # 同词多次只算命中 1 个关键词
    assert r.primary == "digital_economy"
    assert r.confidence == 1.0


def test_match_reason_lists_keywords(engine: DimensionEngine) -> None:
    r = engine.classify("medicamento e vacina")
    assert r.match_reason is not None
    assert "healthcare" in r.match_reason
    # 命中的词在 reason 里
    assert "medicamento" in r.match_reason or "vacina" in r.match_reason


# ─── 大小写 / 重音 ─────────────────────────────────────────────────────


def test_accent_and_case_insensitive(engine: DimensionEngine) -> None:
    """重音 + 大写 + 无重音 都能命中。"""
    assert engine.classify("INFORMÁTICA").primary == "digital_economy"
    assert engine.classify("informatica").primary == "digital_economy"
    assert engine.classify("Informática").primary == "digital_economy"


def test_low_confidence_flag(engine: DimensionEngine) -> None:
    """命中多个维度、primary 占比低 → low_confidence=True。"""
    # 数字 1 词 + 医疗 1 词 + 制造 1 词 → 各 1/3 ≈ 0.33 < 0.5
    text = "software medicamento maquina"
    r = engine.classify(text)
    assert r.confidence < 0.5
    assert r.low_confidence is True


# ─── 真实招标标的 ──────────────────────────────────────────────────────


def test_real_objeto_compra_samples(engine: DimensionEngine) -> None:
    """用真实抓取里见过的 objeto_compra。"""
    # 之前真实数据:"Aquisição de equipamentos de informática para o Hospital Regional"
    # 同时含 informatica(数字)+ hospital(医疗)
    r = engine.classify("Aquisição de equipamentos de informática para o Hospital Regional")
    assert r.primary in ("digital_economy", "healthcare")
    assert r.confidence > 0


def test_singleton_cached() -> None:
    assert get_default_dimension_engine() is get_default_dimension_engine()
