"""PNCP 采购明细 fetcher。

Endpoint: ``GET /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens``

不像 ``/api/consulta/v1/*``,这个 endpoint **不被 F5 BIG-IP ASM 拦**(P1.5
反爬调研时实测过),所以走普通 :class:`HttpClient`,**不需要
BrowserSession**,跑得比 publicacao 快很多。

返回值是一个 list of dict(每个 dict 是一条明细),不分页。
"""
from __future__ import annotations

from typing import Any

from ..core.http_client import HttpClient
from ..core.logger import logger
from ..core.settings import load_settings

_DEFAULT_V1_BASE = "https://pncp.gov.br/api/pncp/v1"


def _v1_base_url() -> str:
    """``settings.yaml`` 里 ``pncp.v1_base_url`` 优先,否则用默认值。"""
    try:
        cfg = load_settings().get("pncp", {}) or {}
        return str(cfg.get("v1_base_url", _DEFAULT_V1_BASE)).rstrip("/")
    except Exception:
        return _DEFAULT_V1_BASE


def _build_url(cnpj: str, ano: int, seq: int) -> str:
    return f"{_v1_base_url()}/orgaos/{cnpj}/compras/{ano}/{seq}/itens"


async def fetch_itens(
    cnpj: str,
    ano: int,
    seq: int,
    *,
    client: HttpClient | None = None,
) -> list[dict[str, Any]]:
    """拉取某个采购的明细列表。

    Args:
        cnpj: 14 位机构 CNPJ。
        ano: 采购年份(4 位)。
        seq: 采购序号(int)。
        client: 已就绪的 :class:`HttpClient`(orchestrator 共享一个);
            ``None`` 时本函数内建临时实例(单测方便)。

    Returns:
        明细 dict 列表,API 原样不做转换。空响应 / 非 list 形态返回 ``[]``,
        不抛错(orchestrator 把这种当"无明细")。

    Raises:
        HttpError: 4xx (含 404) / 5xx / 网络错。orchestrator 应该捕获后
            log warning 并跳过这条记录,不中断整体流程。
    """
    url = _build_url(cnpj, ano, seq)

    log = logger.bind(endpoint="itens", cnpj=cnpj, ano=ano, seq=seq, url=url)
    log.info("fetcher.itens.start")

    if client is not None:
        data = await client.get_json(url)
    else:
        async with HttpClient() as c:
            data = await c.get_json(url)

    if data is None:
        log.warning("fetcher.itens.no_content")
        return []

    if not isinstance(data, list):
        log.bind(actual_type=type(data).__name__).warning("fetcher.itens.unexpected_shape")
        return []

    log.bind(n_items=len(data)).info("fetcher.itens.done")
    return data


__all__ = ["fetch_itens"]
