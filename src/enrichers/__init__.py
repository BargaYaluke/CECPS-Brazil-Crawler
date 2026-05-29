"""富化层(enrichers)。

通过外部数据源 / 离线配置 / 本地维表给已入库记录补字段。已实现:

    region.py     UF → macro region + GDP 分层(离线查 regions.yaml,无需 API)
    fx.py         BRL → CNY 汇率(AwesomeAPI;BCB PTAX 实测不含 CNY)
    cnpj.py       BrasilAPI CNPJ 机构/供应商画像(razão social/CNAE/porte/…)
    catalogo.py   itens 裸编码 → catalogo_compras 标准品类树的可读类目名

规则(CLAUDE.md §1):enricher 只读不写主表的业务字段,输出补全值;真正的
DB 遍历 / 落库在 :mod:`src.pipeline.orchestrator`(``run_enrichment``)。
"""
from .catalogo import derive_categoria
from .cnpj import fetch_cnpj_profile, normalize_cnpj
from .fx import fetch_brl_to_cny_rate, to_cny
from .region import lookup_region

__all__ = [
    "lookup_region",
    "fetch_brl_to_cny_rate",
    "to_cny",
    "fetch_cnpj_profile",
    "normalize_cnpj",
    "derive_categoria",
]
