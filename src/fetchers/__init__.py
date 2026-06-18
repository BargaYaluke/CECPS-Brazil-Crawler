"""采集层(fetchers)。

每个 fetcher 对应一个 PNCP endpoint:
    pncp_publicacao.py     /api/consulta/v1/contratacoes/publicacao   ✅ P1
    pncp_atualizacao.py    /api/consulta/v1/contratacoes/atualizacao   ✅
    pncp_proposta.py       /api/consulta/v1/contratacoes/proposta     ✅
    pncp_itens.py          /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens   ✅

规则: fetcher 只负责拉数据 + 写入原始落地区,不做过滤 / 富化 / 分类。
"""

from .pncp_atualizacao import fetch_atualizacao, fetch_atualizacao_all
from .pncp_itens import fetch_itens
from .pncp_proposta import fetch_proposta, fetch_proposta_all
from .pncp_publicacao import fetch_publicacao, fetch_publicacao_all

__all__ = [
    "fetch_publicacao",
    "fetch_publicacao_all",
    "fetch_atualizacao",
    "fetch_atualizacao_all",
    "fetch_proposta",
    "fetch_proposta_all",
    "fetch_itens",
]
