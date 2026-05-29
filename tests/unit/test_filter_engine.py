"""tests/unit/test_filter_engine.py — 过滤引擎单元测试。

覆盖任务规范要求的 6 个点:
    * 每条 F 规则的命中与不命中
    * 每条 T 规则的标注
    * 多规则同时命中时的优先级(硬过滤优先)
    * 大小写、重音变体
    * regex 单词边界
    * fallback_field 行为
"""
from __future__ import annotations

import pytest

from src.filters.filter_engine import (
    FilterEngine,
    FilterResult,
    FilterRules,
    get_default_engine,
    load_rules,
    normalize,
)


# ─── normalize ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Pregão - Eletrônico", "pregao eletronico"),
        ("Concorrência", "concorrencia"),
        ("Exclusiva ME/EPP", "exclusiva me epp"),
        ("São Paulo", "sao paulo"),
        ("  multi   space  ", "multi space"),
        ("Pregão  -  Eletrônico", "pregao eletronico"),
        ("", ""),
        (None, ""),
        (123, ""),
        ("Lei 14.133/2021, Art. 28, I", "lei 14.133 2021, art. 28, i"),
    ],
)
def test_normalize(raw, expected) -> None:
    assert normalize(raw) == expected


# ─── 默认 yaml 加载 ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def engine() -> FilterEngine:
    """加载真实 config/filter_rules.yaml。"""
    # 不用 get_default_engine 单例,避免污染其它测试
    return FilterEngine(load_rules())


def test_default_yaml_has_6_hard_and_3_soft(engine: FilterEngine) -> None:
    """yaml 必须有 6 条硬过滤 + 3 条软标注。"""
    assert len(engine.rules.hard_filters) == 6
    assert len(engine.rules.soft_tags) == 3
    assert {r.id for r in engine.rules.hard_filters} == {"F001", "F002", "F003", "F004", "F005", "F006"}
    assert {r.id for r in engine.rules.soft_tags} == {"T001", "T002", "T003"}


# ─── F 规则命中 ────────────────────────────────────────────────────────


def test_f001_dispensa_hit(engine: FilterEngine) -> None:
    r = engine.evaluate({"modalidade_nome": "Dispensa de Licitação"})
    assert r.keep is False
    assert r.hit_rule_id == "F001"
    assert "免标" in r.reason or "中企" in r.reason


def test_f001_dispensa_miss(engine: FilterEngine) -> None:
    r = engine.evaluate({"modalidade_nome": "Pregão - Eletrônico"})
    assert r.keep is True
    assert r.hit_rule_id is None


def test_f002_inexigibilidade_hit(engine: FilterEngine) -> None:
    r = engine.evaluate({"modalidade_nome": "Inexigibilidade"})
    assert r.keep is False
    assert r.hit_rule_id == "F002"


def test_f003_me_epp_on_amparo_legal(engine: FilterEngine) -> None:
    r = engine.evaluate(
        {"amparo_legal_nome": "Lei 123 - exclusivo para ME/EPP"}
    )
    assert r.keep is False
    assert r.hit_rule_id == "F003"


def test_f003_me_epp_miss_clean_amparo(engine: FilterEngine) -> None:
    r = engine.evaluate({"amparo_legal_nome": "Lei 14.133/2021, Art. 28, I"})
    assert r.keep is True


def test_f004_cota_reservada_via_edital_text(engine: FilterEngine) -> None:
    r = engine.evaluate(
        {
            "modalidade_nome": "Pregão - Eletrônico",
            "edital_text": "...há cota reservada de 25% para...",
        }
    )
    assert r.keep is False
    assert r.hit_rule_id == "F004"


def test_f005_ppb_regex_word_boundary_hit(engine: FilterEngine) -> None:
    """PPB 单词边界 — 'cumprir PPB obrigatório' 命中。"""
    r = engine.evaluate(
        {"edital_text": "Item deve cumprir PPB obrigatório."}
    )
    assert r.keep is False
    assert r.hit_rule_id == "F005"


def test_f005_ppb_word_boundary_miss(engine: FilterEngine) -> None:
    """PPB 单词边界 — 'PPBank' 不应该命中。"""
    r = engine.evaluate({"edital_text": "PPBank S.A. fornece o serviço."})
    assert r.keep is True


def test_f005_ipb_regex_hit(engine: FilterEngine) -> None:
    r = engine.evaluate({"edital_text": "Conforme IPB do MCTI..."})
    assert r.keep is False
    assert r.hit_rule_id == "F005"


def test_f005_processo_produtivo_basico_hit(engine: FilterEngine) -> None:
    r = engine.evaluate(
        {"edital_text": "Atender ao Processo Produtivo Básico."}
    )
    assert r.keep is False
    assert r.hit_rule_id == "F005"


def test_f006_prazo_exiguo_hit(engine: FilterEngine) -> None:
    r = engine.evaluate({"edital_text": "Prazo de Entrega Exíguo, 5 dias úteis."})
    assert r.keep is False
    assert r.hit_rule_id == "F006"


# ─── T 规则标注 ────────────────────────────────────────────────────────


def test_t001_credenciamento(engine: FilterEngine) -> None:
    r = engine.evaluate({"modalidade_nome": "Credenciamento"})
    assert r.keep is True
    assert r.tags == {"is_long_term_opportunity": True}


def test_t002_concorrencia(engine: FilterEngine) -> None:
    r = engine.evaluate({"modalidade_nome": "Concorrência - Eletrônica"})
    assert r.keep is True
    assert r.tags == {"is_competitive_bid": True}


def test_t003_pregao_eletronico_with_hyphen(engine: FilterEngine) -> None:
    """真实 API 字段是 'Pregão - Eletrônico'(带横杠),
    关键词 'Pregão Eletrônico' 经 normalize 后等价 → 命中。"""
    r = engine.evaluate({"modalidade_nome": "Pregão - Eletrônico"})
    assert r.keep is True
    assert r.tags == {"is_e_auction": True}


def test_t003_pregao_presencial_miss(engine: FilterEngine) -> None:
    """Pregão - Presencial 不应该命中 T003(只针对 Eletrônico)。"""
    r = engine.evaluate({"modalidade_nome": "Pregão - Presencial"})
    assert r.keep is True
    assert r.tags == {}


# ─── 多规则优先级(硬过滤优先)──────────────────────────────────────────


def test_hard_filter_short_circuits_before_soft_tags(engine: FilterEngine) -> None:
    """同时命中 F001(Dispensa)+ 任何 T 规则 → 只返回 F001 命中,
    tags 为空,因为硬过滤已经决定 keep=False。"""
    r = engine.evaluate(
        {
            "modalidade_nome": "Dispensa - Credenciamento - Concorrência",
        }
    )
    assert r.keep is False
    assert r.hit_rule_id == "F001"
    assert r.tags == {}


def test_multiple_soft_tags_all_applied(engine: FilterEngine) -> None:
    """同时命中多个软标注 → tags 全部累积。"""
    r = engine.evaluate(
        {"modalidade_nome": "Credenciamento - Concorrência - Pregão Eletrônico"}
    )
    assert r.keep is True
    # 三个 T 规则全部命中
    assert r.tags == {
        "is_long_term_opportunity": True,
        "is_competitive_bid": True,
        "is_e_auction": True,
    }


# ─── 大小写 / 重音变体 ──────────────────────────────────────────────────


def test_case_insensitive(engine: FilterEngine) -> None:
    r = engine.evaluate({"modalidade_nome": "DISPENSA"})
    assert r.keep is False
    assert r.hit_rule_id == "F001"


def test_accent_insensitive_concorrencia(engine: FilterEngine) -> None:
    """关键词 'Concorrência' 跟 'concorrencia'(无重音)等价。"""
    r = engine.evaluate({"modalidade_nome": "concorrencia eletronica"})
    assert r.keep is True
    assert r.tags == {"is_competitive_bid": True}


def test_accent_insensitive_pregao(engine: FilterEngine) -> None:
    """'pregao eletronico'(无重音)能命中 T003。"""
    r = engine.evaluate({"modalidade_nome": "pregao eletronico"})
    assert r.keep is True
    assert r.tags == {"is_e_auction": True}


# ─── fallback_field 行为 ───────────────────────────────────────────────


def test_f003_falls_back_to_edital_text() -> None:
    """F003 主字段 amparo_legal_nome 为空 → 用 fallback edital_text 检查。"""
    engine = FilterEngine(load_rules())
    r = engine.evaluate(
        {
            "amparo_legal_nome": "",
            "edital_text": "...participação exclusiva para microempresa...",
        }
    )
    assert r.keep is False
    assert r.hit_rule_id == "F003"


def test_f003_uses_primary_field_when_present() -> None:
    """主字段非空 → 用主字段,不查 fallback。"""
    engine = FilterEngine(load_rules())
    r = engine.evaluate(
        {
            "amparo_legal_nome": "Lei 14.133/2021, Art. 28, I",  # 干净
            "edital_text": "Exclusiva ME/EPP",  # 不应该被读到
        }
    )
    # F003 在主字段没命中 → 主字段不为空,不查 fallback → keep=True
    assert r.keep is True


def test_f003_missing_both_fields(engine: FilterEngine) -> None:
    """主字段和 fallback 都缺 → 这条规则不命中。"""
    r = engine.evaluate({"modalidade_nome": "Pregão - Eletrônico"})
    assert r.keep is True


# ─── 二次过滤 re_evaluate ──────────────────────────────────────────────


def test_re_evaluate_triggers_on_edital_text(engine: FilterEngine) -> None:
    """一次 evaluate 没命中,但 PDF 文本里有 PPB → re_evaluate 命中 F005。"""
    data = {"modalidade_nome": "Pregão - Eletrônico"}
    first = engine.evaluate(data)
    assert first.keep is True

    second = engine.re_evaluate(data, edital_text="...exigir PPB conforme...")
    assert second.keep is False
    assert second.hit_rule_id == "F005"


def test_re_evaluate_does_not_run_f001_f002(engine: FilterEngine) -> None:
    """re_evaluate 只跑 F003-F006,不重跑 F001/F002 / soft tags。"""
    # modalidade_nome 含 Dispensa 通常 evaluate 时就命中,但 re_evaluate
    # 是 evaluate **之后**用 PDF 补做 — 这里测试它只看 F003-F006。
    data = {"modalidade_nome": "Dispensa"}
    r = engine.re_evaluate(data, edital_text="...nothing exciting...")
    # F001 用 modalidade_nome 而非 edital_text,不在 RE_EVALUATE_RULE_IDS 里
    assert r.keep is True


# ─── 真实数据 sanity check ─────────────────────────────────────────────


def test_real_publicacao_record_is_kept_with_t003(engine: FilterEngine) -> None:
    """50 条真实抓取里的一条:Pregão - Eletrônico → keep=True 且 T003 命中。"""
    real_record = {
        "modalidade_nome": "Pregão - Eletrônico",
        "amparo_legal_nome": "Lei 14.133/2021, Art. 28, I",
        "objeto_compra": "Aquisição de equipamentos",
    }
    r = engine.evaluate(real_record)
    assert r.keep is True
    assert r.tags == {"is_e_auction": True}
    assert r.hit_rule_id is None


def test_get_default_engine_cached() -> None:
    """单例缓存:两次拿到的是同一个对象。"""
    a = get_default_engine()
    b = get_default_engine()
    assert a is b
