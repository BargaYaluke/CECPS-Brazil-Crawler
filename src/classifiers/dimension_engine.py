"""六大维度分类引擎。

读 ``config/dimension_keywords.yaml``,对一段文本(招标标的 + 补充信息)
做关键词匹配,输出六大维度标签。**纯函数**,不依赖外部状态,易测试。

六大维度:
    digital_economy          数字经济
    healthcare               医疗医药
    high_end_manufacturing   高端制造
    commodities              大宗商贸
    cross_border_ecommerce   跨境电商
    culture_sports           文化体育

算法(简单可解释):
    1. text 经 :func:`src.filters.filter_engine.normalize` 规范化
    2. 每个维度数命中的关键词,得分 = 命中数 × weight
    3. primary = 得分最高的维度(全 0 → 无法分类,primary=None)
    4. confidence = primary 得分 / 所有维度总得分(0-1)
    5. secondary = 其它得分占比 ≥ secondary_min_score 的维度
    6. match_reason = 各维度命中的关键词(可解释)

输出对应 ``contratacoes`` 的 4 个字段:
    dimension_primary / dimension_secondary / dimension_confidence / dimension_match_reason
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..core.exceptions import ClassifierError
from ..core.logger import logger
from ..core.settings import DEFAULT_CONFIG_DIR
from ..filters.filter_engine import normalize  # 复用同一套 normalize(filters 是上游)


# ─── 规则模型 ──────────────────────────────────────────────────────────


class DimensionDef(BaseModel):
    """单个维度的定义。"""

    model_config = ConfigDict(extra="allow")
    nome_zh: str | None = None
    weight: float = 1.0
    keywords_pt: list[str] = Field(default_factory=list)
    keywords_zh: list[str] = Field(default_factory=list)
    catmat_codes: list[str] = Field(default_factory=list)


class DimensionThresholds(BaseModel):
    model_config = ConfigDict(extra="allow")
    low_confidence_below: float = 0.5
    secondary_min_score: float = 0.25


class DimensionRules(BaseModel):
    """整个 ``dimension_keywords.yaml`` 的根。"""

    model_config = ConfigDict(extra="allow")
    dimensions: dict[str, DimensionDef]
    thresholds: DimensionThresholds = Field(default_factory=DimensionThresholds)


class DimensionResult(BaseModel):
    """单条记录的分类结果(对应 contratacoes 的 4 个 dimension 字段)。"""

    model_config = ConfigDict(extra="forbid")

    primary: str | None = None
    secondary: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    match_reason: str | None = None
    low_confidence: bool = False
    """confidence < 阈值 → True,建议人工复核。"""


# ─── 加载 yaml ─────────────────────────────────────────────────────────


def load_dimension_rules(path: Path | None = None) -> DimensionRules:
    """加载 ``dimension_keywords.yaml`` 为 :class:`DimensionRules`。"""
    if path is None:
        path = DEFAULT_CONFIG_DIR / "dimension_keywords.yaml"
    if not path.exists():
        raise ClassifierError(f"dimension_keywords.yaml not found at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ClassifierError(f"failed to parse {path}: {exc}") from exc
    try:
        return DimensionRules.model_validate(data)
    except Exception as exc:
        raise ClassifierError(f"invalid dimension_keywords.yaml schema: {exc}") from exc


# ─── 分类引擎 ──────────────────────────────────────────────────────────


class DimensionEngine:
    """对文本应用 :class:`DimensionRules` 打六大维度标签。"""

    def __init__(self, rules: DimensionRules) -> None:
        self.rules = rules
        # 预 normalize 所有关键词,加速多次调用:dim -> [(orig_kw, norm_kw), ...]
        self._norm_keywords: dict[str, list[tuple[str, str]]] = {}
        for dim, ddef in rules.dimensions.items():
            kws = [*ddef.keywords_pt, *ddef.keywords_zh]
            self._norm_keywords[dim] = [(kw, normalize(kw)) for kw in kws if normalize(kw)]

    def classify(self, text: str | None) -> DimensionResult:
        """对一段文本分类。

        Args:
            text: 待分类文本(通常 objeto_compra + informacao_complementar 拼接)。

        Returns:
            :class:`DimensionResult`。无任何关键词命中 → primary=None。
        """
        norm_text = normalize(text)
        if not norm_text:
            return DimensionResult(primary=None, confidence=0.0, match_reason=None)

        # 每个维度命中的关键词 + 加权得分
        hits: dict[str, list[str]] = {}
        scores: dict[str, float] = {}
        for dim, kw_pairs in self._norm_keywords.items():
            matched = [orig for orig, norm_kw in kw_pairs if norm_kw in norm_text]
            if matched:
                hits[dim] = matched
                scores[dim] = len(matched) * self.rules.dimensions[dim].weight

        if not scores:
            return DimensionResult(primary=None, confidence=0.0, match_reason=None)

        total = sum(scores.values())
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        primary = ranked[0][0]
        confidence = scores[primary] / total if total else 0.0

        sec_min = self.rules.thresholds.secondary_min_score
        secondary = [d for d, s in ranked[1:] if (s / total) >= sec_min]

        # 可解释的 reason:各维度命中词
        reason = "; ".join(
            f"{d}:[{','.join(hits[d])}]" for d, _ in ranked
        )

        return DimensionResult(
            primary=primary,
            secondary=secondary,
            confidence=round(confidence, 4),
            match_reason=reason,
            low_confidence=confidence < self.rules.thresholds.low_confidence_below,
        )


@lru_cache(maxsize=1)
def get_default_dimension_engine() -> DimensionEngine:
    """默认配置目录加载的引擎单例。"""
    engine = DimensionEngine(load_dimension_rules())
    logger.bind(dimensions=list(engine.rules.dimensions.keys())).info(
        "classifier.dimension_engine.loaded"
    )
    return engine


__all__ = [
    "DimensionDef",
    "DimensionThresholds",
    "DimensionRules",
    "DimensionResult",
    "DimensionEngine",
    "load_dimension_rules",
    "get_default_dimension_engine",
]
