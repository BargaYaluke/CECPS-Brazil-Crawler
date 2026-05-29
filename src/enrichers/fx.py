"""汇率富化:BRL → CNY(人民币)估值。

数据源说明(2026-05-28 实测,典型的"别信文档先探接口"):
    CLAUDE.md 原计划用 **BCB**(Banco Central)汇率,但 BCB PTAX 的开放 OData
    (``olinda.bcb.gov.br/.../PTAX``)**只含 10 种主流货币,不含 CNY**
    —— ``CotacaoMoedaPeriodo(moeda='CNY')`` 返回空。
    故 BRL→CNY 改用 **AwesomeAPI**(``economia.awesomeapi.com.br``,巴西常用、
    免认证、单次调用即得),取 ``BRLCNY.bid``(1 BRL = ? CNY)。

填充目标列:``contratacoes.valor_cny_estimado = valor_total_estimado × rate``。
DB 遍历更新在 :func:`src.pipeline.orchestrator.run_enrichment`。
"""
from __future__ import annotations

from typing import Any

from ..core.http_client import HttpClient
from ..core.logger import logger
from ..core.settings import get_enrichment_settings


async def fetch_brl_to_cny_rate(*, client: HttpClient | None = None) -> float | None:
    """取实时汇率 ``1 BRL = ? CNY``(AwesomeAPI 的 ``BRLCNY.bid``)。

    Args:
        client: 已就绪的 :class:`HttpClient`;``None`` 时内建临时实例。

    Returns:
        汇率(float,如 ``1.34``);取不到 / 解析失败时返回 ``None``(调用方降级)。
    """
    cfg = get_enrichment_settings()
    url = str(cfg["fx_brl_cny_url"])

    async def _call(c: HttpClient) -> float | None:
        data = await c.get_json(url)
        if not isinstance(data, dict):
            logger.bind(url=url).warning("enricher.fx.unexpected_shape")
            return None
        node = data.get("BRLCNY") or next(iter(data.values()), None)
        if not isinstance(node, dict):
            logger.bind(url=url).warning("enricher.fx.no_quote")
            return None
        bid = node.get("bid")
        try:
            rate = float(bid)
        except (TypeError, ValueError):
            logger.bind(bid=bid).warning("enricher.fx.bad_rate")
            return None
        logger.bind(rate=rate, create_date=node.get("create_date")).info("enricher.fx.rate")
        return rate

    if client is not None:
        return await _call(client)
    async with HttpClient(rate_limit_per_second=0) as c:
        return await _call(c)


def to_cny(valor_brl: float | None, rate: float | None) -> float | None:
    """把 BRL 金额按汇率换算成 CNY(保留 2 位);任一为 None 返回 None。"""
    if valor_brl is None or rate is None:
        return None
    return round(float(valor_brl) * float(rate), 2)


__all__ = ["fetch_brl_to_cny_rate", "to_cny"]
