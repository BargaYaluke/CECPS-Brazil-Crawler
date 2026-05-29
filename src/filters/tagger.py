"""把 :class:`FilterResult` 应用到 ContratacaoIn / Contratacao 对象上。

分两种情形:
    - ``result.keep is True``:把 ``result.tags`` 里的 ``set_field → set_value``
      逐个赋到 record 上(``is_e_auction`` / ``is_long_term_opportunity`` 等)。
    - ``result.keep is False``:写 ``filter_reason`` 和 ``filter_rule_id``。
      要不要真正入主表 — 由 orchestrator 根据 ``settings.filter.mode`` 决定。

这层只做"赋值",不做"决定是否入库"。
"""
from __future__ import annotations

from typing import Any

from ..core.logger import logger
from .filter_engine import FilterResult


def apply_result(record: Any, result: FilterResult) -> None:
    """把 result 写到 record 字段上(in-place,适用 pydantic / dataclass / SA ORM)。

    Args:
        record: 任何有可写字段的对象(``ContratacaoIn`` / ``Contratacao``)。
            缺字段时静默跳过(给将来 schema 变化兜底)。
        result: :class:`FilterResult`。
    """
    if result.keep:
        for field_name, value in result.tags.items():
            _try_set(record, field_name, value)
        return

    _try_set(record, "filter_reason", result.reason)
    _try_set(record, "filter_rule_id", result.hit_rule_id)


def _try_set(record: Any, field_name: str, value: Any) -> None:
    """在能赋值的情况下赋值;否则只打 warning,不抛错。"""
    if hasattr(record, field_name):
        setattr(record, field_name, value)
    else:
        logger.bind(field=field_name, record_type=type(record).__name__).warning(
            "filter.tagger.field_not_found"
        )


__all__ = ["apply_result"]
