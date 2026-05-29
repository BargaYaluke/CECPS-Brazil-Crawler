"""机构/供应商画像富化:BrasilAPI CNPJ 查询。

数据源:``https://brasilapi.com.br/api/cnpj/v1/{cnpj}``(免认证)。
返回 razão social / nome fantasia / CNAE / porte / natureza jurídica /
município / uf / situação cadastral 等(底层是 Receita Federal 数据)。

用途:给 ``orgaos`` 机构画像表补权威名称 / 所在地(orgao 富化时调用),
也可复用到供应商(``contratos.ni_fornecedor``)画像。

返回精简后的 dict(只挑业务关心的字段);查不到 / CNPJ 非法返回 ``None``。
"""
from __future__ import annotations

import re
from typing import Any

from ..core.exceptions import HttpError
from ..core.http_client import HttpClient
from ..core.logger import logger
from ..core.settings import get_enrichment_settings

# BrasilAPI 返回里我们关心的字段(精简画像)
_PROFILE_KEYS = (
    "razao_social",
    "nome_fantasia",
    "cnae_fiscal",
    "cnae_fiscal_descricao",
    "porte",
    "natureza_juridica",
    "municipio",
    "uf",
    "situacao_cadastral",
    "data_inicio_atividade",
    "capital_social",
)


def normalize_cnpj(cnpj: str | None) -> str | None:
    """抽出 14 位数字;非法返回 ``None``。"""
    if not cnpj:
        return None
    digits = re.sub(r"\D", "", cnpj)
    return digits if len(digits) == 14 else None


async def fetch_cnpj_profile(
    cnpj: str | None, *, client: HttpClient | None = None
) -> dict[str, Any] | None:
    """查 CNPJ 画像。

    Args:
        cnpj: 机构/供应商 CNPJ(可带符号,内部清洗成 14 位)。
        client: 已就绪的 :class:`HttpClient`;``None`` 时内建临时实例。

    Returns:
        精简画像 dict(见 :data:`_PROFILE_KEYS`);CNPJ 非法 / 404 / 解析失败返回 ``None``。

    Raises:
        HttpError: 非 404 的 HTTP 错误(429/5xx 等已在 HttpClient 内重试)。
            调用方(orchestrator)应捕获后 log warning 并跳过该机构。
    """
    digits = normalize_cnpj(cnpj)
    if digits is None:
        return None

    cfg = get_enrichment_settings()
    base = str(cfg["brasilapi_base_url"]).rstrip("/")
    url = f"{base}/cnpj/v1/{digits}"
    rate = float(cfg["rate_limit_per_sec"])

    async def _call(c: HttpClient) -> dict[str, Any] | None:
        try:
            data = await c.get_json(url)
        except HttpError as exc:
            if exc.status_code == 404:
                logger.bind(cnpj=digits).warning("enricher.cnpj.not_found")
                return None
            raise
        if not isinstance(data, dict):
            return None
        profile = {k: data.get(k) for k in _PROFILE_KEYS}
        profile["cnpj"] = digits
        return profile

    if client is not None:
        return await _call(client)
    async with HttpClient(rate_limit_per_second=rate) as c:
        return await _call(c)


__all__ = ["fetch_cnpj_profile", "normalize_cnpj"]
