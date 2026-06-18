"""采集层(fetchers)。

每个 fetcher 对应一个 PNCP endpoint:
    pncp_publicacao.py     /api/consulta/v1/contratacoes/publicacao   ✅ P1
    pncp_atualizacao.py    /api/consulta/v1/contratacoes/atualizacao   ✅
    pncp_proposta.py       /api/consulta/v1/contratacoes/proposta     ✅
    pncp_itens.py          /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens   ✅
    pncp_contratos.py      /api/consulta/v1/contratos                  ✅ (实测路径)
    pncp_pca.py            /api/consulta/v1/pca/atualizacao            ✅ (实测路径)
    edital_pdf.py          /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos  ✅

规则: fetcher 只负责拉数据 + 写入原始落地区,不做过滤 / 富化 / 分类。
(edital_pdf 额外做 PDF 文本提取,但仍属"采集",解析结果交给上层。)
"""

from .edital_pdf import (
    download_and_extract_text,
    fetch_arquivos,
    fetch_edital_text,
    select_main_edital,
)
from .pncp_atualizacao import fetch_atualizacao, fetch_atualizacao_all
from .pncp_contratos import fetch_contratos, fetch_contratos_all
from .pncp_itens import fetch_itens
from .pncp_pca import fetch_pca, fetch_pca_all
from .pncp_proposta import fetch_proposta, fetch_proposta_all
from .pncp_publicacao import fetch_publicacao, fetch_publicacao_all

__all__ = [
    "fetch_publicacao",
    "fetch_publicacao_all",
    "fetch_atualizacao",
    "fetch_atualizacao_all",
    "fetch_proposta",
    "fetch_proposta_all",
    "fetch_contratos",
    "fetch_contratos_all",
    "fetch_pca",
    "fetch_pca_all",
    "fetch_itens",
    "fetch_arquivos",
    "select_main_edital",
    "download_and_extract_text",
    "fetch_edital_text",
]
