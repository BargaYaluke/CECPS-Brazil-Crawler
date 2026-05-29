"""过滤标注层(filters)。

业务规则单一事实源: ``config/filter_rules.yaml``。
本层只负责执行 yaml 规则,严禁在代码里硬编码关键词。

模块:
    filter_engine.py  FilterRules / FilterEngine.evaluate / re_evaluate
    tagger.py         apply_result — 把 FilterResult 写到 record 字段
    filtered_log.py   write_filtered_log — Parquet 按日分区,只追加
"""

from .filter_engine import (
    FilterEngine,
    FilterResult,
    FilterRules,
    HardRule,
    SoftTagRule,
    get_default_engine,
    load_rules,
    normalize,
)
from .filtered_log import write_filtered_log
from .tagger import apply_result

__all__ = [
    "FilterEngine",
    "FilterResult",
    "FilterRules",
    "HardRule",
    "SoftTagRule",
    "load_rules",
    "get_default_engine",
    "normalize",
    "apply_result",
    "write_filtered_log",
]
