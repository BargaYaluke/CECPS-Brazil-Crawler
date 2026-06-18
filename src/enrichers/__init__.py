"""富化层(enrichers)。

通过外部数据源 / 离线配置给已入库 contratacoes 补字段。已实现:

    region.py     UF → macro region + GDP 分层(离线查 regions.yaml,无需 API)
    fx.py         BRL → CNY 汇率(AwesomeAPI;填 valor_cny_estimado)
    deadline.py   截止日 → 剩余天数 + 时效状态(离线)
    translate.py  葡→中标的梗概(DeepSeek + 离线词典,填 translation_cache)

规则:enricher 只读不写主表的业务字段,输出补全值;真正的
DB 遍历 / 落库在 :mod:`src.pipeline.orchestrator`(``run_enrichment`` / ``run_translate``)。
"""
from .deadline import classify_deadline, status_zh
from .fx import fetch_brl_to_cny_rate, to_cny
from .region import lookup_region

__all__ = [
    "lookup_region",
    "fetch_brl_to_cny_rate",
    "to_cny",
    "classify_deadline",
    "status_zh",
]
