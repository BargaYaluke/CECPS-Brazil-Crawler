"""六大维度分类层(classifiers)。

把招标记录打到六个业务维度上,关键词与 CATMAT 映射来自
``config/dimension_keywords.yaml``。

维度:
    digital_economy          数字经济
    healthcare               医疗医药
    high_end_manufacturing   高端制造
    commodities              大宗商贸
    cross_border_ecommerce   跨境电商
    culture_sports           文化体育

输出字段:
    dimension_primary, dimension_secondary,
    dimension_confidence, dimension_match_reason
"""

from .dimension_engine import (
    DimensionEngine,
    DimensionResult,
    DimensionRules,
    get_default_dimension_engine,
    load_dimension_rules,
)

__all__ = [
    "DimensionEngine",
    "DimensionResult",
    "DimensionRules",
    "load_dimension_rules",
    "get_default_dimension_engine",
]
