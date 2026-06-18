"""过滤与标注引擎。

读 ``config/filter_rules.yaml`` 的规则,对单条 record dict 求值,返回
:class:`FilterResult`。**完全是纯函数**,不依赖外部状态,易测试。

两遍过滤:
    1. :meth:`FilterEngine.evaluate` — 入库前,只用 API 字段
       (F001/F002 用 modalidade_nome,F003 用 amparo_legal_nome,
       所有 soft_tags 用 modalidade_nome)
    2. :meth:`FilterEngine.re_evaluate` — PDF 解析后,拿到 ``edital_text``,
       重跑 F003/F004/F005/F006

字符串匹配统一过 :func:`normalize`:
    NFKD 去重音 → casefold → "- _ /" 替换为空格 → 压缩连续空格
这样 "Pregão - Eletrônico" 和关键词 "Pregão Eletrônico" 都规范成
"pregao eletronico",自然匹配。
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..core.exceptions import FilterError
from ..core.logger import logger
from ..core.settings import DEFAULT_CONFIG_DIR

MatchType = Literal["contains_any", "regex_any", "equals", "in_list"]


# ─── 字符串规范化 ──────────────────────────────────────────────────────


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[-_/]+")


def normalize(value: Any) -> str:
    """统一规范化字符串以做不区分大小写 / 重音 / 标点的包含匹配。

    步骤:
        1. ``None`` / 非字符串 → ``""``
        2. ``unicodedata.normalize("NFKD")`` 把 "Pregão" 拆成 "Pregao" + 重音符
        3. 去掉所有 combining marks(重音)
        4. ``str.casefold()`` (比 .lower() 更激进,处理 ß / ı 等)
        5. ``- _ /`` 全替换成空格(让 "Pregão - Eletrônico" 跟 "Pregão Eletrônico" 等价)
        6. 压缩连续空格 + 去首尾空格

    Examples::

        >>> normalize("Pregão - Eletrônico")
        'pregao eletronico'
        >>> normalize("Concorrência")
        'concorrencia'
        >>> normalize("Exclusiva ME/EPP")
        'exclusiva me epp'
    """
    if not isinstance(value, str) or not value:
        return ""
    s = unicodedata.normalize("NFKD", value)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


# ─── 规则模型 ──────────────────────────────────────────────────────────


class _RuleBase(BaseModel):
    """硬过滤 / 软标注的共用字段。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    field: str
    fallback_field: str | None = None
    match_type: MatchType
    keywords: list[str] | None = None
    patterns: list[str] | None = None


class HardRule(_RuleBase):
    """硬过滤规则;命中即丢弃。"""

    reason: str


class SoftTagRule(_RuleBase):
    """软标注规则;命中给 Contratacao 的某个字段赋值。"""

    set_field: str
    set_value: Any
    label_zh: str | None = None


class FilterRules(BaseModel):
    """整个 ``filter_rules.yaml`` 的根。"""

    model_config = ConfigDict(extra="forbid")

    hard_filters: list[HardRule] = Field(default_factory=list)
    soft_tags: list[SoftTagRule] = Field(default_factory=list)


class FilterResult(BaseModel):
    """单条 record 的求值结果。"""

    model_config = ConfigDict(extra="forbid")

    keep: bool
    """True 表示通过过滤;False 表示命中硬过滤要丢弃。"""

    hit_rule_id: str | None = None
    """命中的硬规则 ID(keep=False 时填)。"""

    reason: str | None = None
    """命中硬规则的人读理由(keep=False 时填)。"""

    tags: dict[str, Any] = Field(default_factory=dict)
    """软标注命中后要给 Contratacao 字段赋的值(keep=True 时填)。"""


# ─── 加载 yaml ─────────────────────────────────────────────────────────


def load_rules(path: Path | None = None) -> FilterRules:
    """加载 ``filter_rules.yaml`` 为 :class:`FilterRules`。

    Args:
        path: 完整文件路径;``None`` 时用 ``CONFIG_DIR/filter_rules.yaml``。
    """
    if path is None:
        path = DEFAULT_CONFIG_DIR / "filter_rules.yaml"
    if not path.exists():
        raise FilterError(f"filter_rules.yaml not found at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise FilterError(f"failed to parse {path}: {exc}") from exc

    try:
        return FilterRules.model_validate(data)
    except Exception as exc:
        raise FilterError(f"invalid filter_rules.yaml schema: {exc}") from exc


# ─── 过滤引擎 ──────────────────────────────────────────────────────────


# 二次过滤覆盖的规则(F003 由于有 fallback_field=edital_text,被两次都跑)
RE_EVALUATE_RULE_IDS = frozenset({"F003", "F004", "F005", "F006"})


class FilterEngine:
    """对 record dict 应用 ``FilterRules``。

    Attributes:
        rules: 加载后的 :class:`FilterRules`。
    """

    def __init__(self, rules: FilterRules) -> None:
        self.rules = rules
        # 预编译 regex,加快多次调用
        self._compiled_patterns: dict[str, list[re.Pattern[str]]] = {}
        for rule in [*rules.hard_filters, *rules.soft_tags]:
            if rule.match_type == "regex_any":
                self._compiled_patterns[rule.id] = [
                    re.compile(p, re.IGNORECASE) for p in (rule.patterns or [])
                ]

    # ─── 单字段值获取(带 fallback)─────────────────────────────────

    @staticmethod
    def _field_value(data: dict[str, Any], rule: _RuleBase) -> str:
        primary = data.get(rule.field)
        if primary not in (None, ""):
            return str(primary)
        if rule.fallback_field:
            fallback = data.get(rule.fallback_field)
            if fallback not in (None, ""):
                return str(fallback)
        return ""

    # ─── 匹配判断 ──────────────────────────────────────────────────

    def _matches(self, rule: _RuleBase, data: dict[str, Any]) -> bool:
        raw_value = self._field_value(data, rule)
        if not raw_value:
            return False

        if rule.match_type == "regex_any":
            patterns = self._compiled_patterns.get(rule.id, [])
            return any(p.search(raw_value) for p in patterns)

        norm_value = normalize(raw_value)
        if not norm_value:
            return False

        if rule.match_type == "contains_any":
            return any(normalize(kw) in norm_value for kw in (rule.keywords or []))
        if rule.match_type == "equals":
            return any(normalize(kw) == norm_value for kw in (rule.keywords or []))
        if rule.match_type == "in_list":
            tokens = {t.strip() for t in norm_value.split(",")}
            return any(normalize(kw) in tokens for kw in (rule.keywords or []))

        raise FilterError(f"unknown match_type {rule.match_type!r} on rule {rule.id}")

    # ─── 主入口 ────────────────────────────────────────────────────

    def evaluate(self, data: dict[str, Any]) -> FilterResult:
        """对 record 跑完整规则。

        Args:
            data: 一条 record 的 dict 形式(直接 API JSON 或 ContratacaoIn.model_dump())。

        Returns:
            :class:`FilterResult`。硬过滤优先 — 任一 hard_filter 命中立即返回 keep=False。
        """
        for rule in self.rules.hard_filters:
            if self._matches(rule, data):
                return FilterResult(
                    keep=False,
                    hit_rule_id=rule.id,
                    reason=rule.reason,
                )

        tags: dict[str, Any] = {}
        for tag_rule in self.rules.soft_tags:
            if self._matches(tag_rule, data):
                tags[tag_rule.set_field] = tag_rule.set_value

        return FilterResult(keep=True, tags=tags)

    def re_evaluate(self, data: dict[str, Any], edital_text: str) -> FilterResult:
        """PDF 解析后的二次过滤,只跑涉及 ``edital_text`` 的规则。

        Args:
            data: 同 :meth:`evaluate`(应该是已经一次过过的 record)。
            edital_text: PDF 解析后的全文文本。

        Returns:
            :class:`FilterResult`。命中任何 F003-F006 → keep=False,
            否则 keep=True(注意 tags 始终为空 — 不重跑软标注)。
        """
        enriched = {**data, "edital_text": edital_text}
        for rule in self.rules.hard_filters:
            if rule.id not in RE_EVALUATE_RULE_IDS:
                continue
            if self._matches(rule, enriched):
                return FilterResult(
                    keep=False,
                    hit_rule_id=rule.id,
                    reason=rule.reason,
                )
        return FilterResult(keep=True)


# ─── 进程级单例 ────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_default_engine() -> FilterEngine:
    """默认配置目录加载的 :class:`FilterEngine` 单例。"""
    engine = FilterEngine(load_rules())
    logger.bind(
        hard_filters=[r.id for r in engine.rules.hard_filters],
        soft_tags=[r.id for r in engine.rules.soft_tags],
    ).info("filter.engine.loaded")
    return engine


__all__ = [
    "normalize",
    "HardRule",
    "SoftTagRule",
    "FilterRules",
    "FilterResult",
    "FilterEngine",
    "load_rules",
    "get_default_engine",
    "RE_EVALUATE_RULE_IDS",
]
