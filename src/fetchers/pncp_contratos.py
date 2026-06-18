"""PNCP ``/api/consulta/v1/contratos`` fetcher。

跟招标(``contratacoes/*``)系列不同 — 这个接口返回**已签合同**(Contrato),
schema 完全不一样(供应商 / 合同金额 / 生效期等)。

**实测发现**(跟原设计描述有出入):

* 路径就是 ``/contratos``,**没有** ``/publicacao`` 后缀
* ``codigoModalidadeContratacao`` **不必填**(合同没有 modalidade 概念)
* 必填:``dataInicial`` + ``dataFinal`` + ``pagina`` + ``tamanhoPagina``
* ``tamanhoPagina`` 最小值 10(< 10 返回 ``Tamanho de página inválido``)

数据规模:7 天约 4.9 万条(对比 publicacao 同期约 4-5 倍)。

返回的 record 用 :class:`ContratoRaw` 校验,扁平化到
:class:`ContratoIn` 入 ``contratos`` 表。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import AsyncIterator

from ..core.exceptions import HttpError
from ..core.logger import logger
from ..core.settings import get_pncp_settings
from .pncp_session import PncpSession

ENDPOINT = "/contratos"
MIN_PAGE_SIZE = 10  # 实测 API 业务校验


def _coerce_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _endpoint_url(pncp_cfg: dict) -> str:
    base = pncp_cfg["base_url"].rstrip("/")
    return f"{base}{ENDPOINT}"


def _cache_path(cache_root: Path, date_start: date, page: int) -> Path:
    """``<cache_root>/contratos/<YYYY-MM-DD>/page_<n>.json``。

    比招标少一层 modalidade(因为合同没 modalidade 维度)。
    """
    return cache_root / "contratos" / date_start.isoformat() / f"page_{page}.json"


def _write_cache(cache_root: Path, date_start: date, page: int, data: dict) -> Path:
    target = _cache_path(cache_root, date_start, page)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


async def fetch_contratos(
    session: PncpSession,
    date_start: date | str,
    date_end: date | str,
    page: int = 1,
    page_size: int = 50,
    cache_root: Path | None = None,
) -> dict | None:
    """单页拉取 ``/contratos``(走浏览器内 fetch 解 F5)。

    Args:
        session: 已 ``async with`` 进入的 :class:`PncpSession`。
        date_start / date_end: 公布日期 ``dataPublicacaoPncp`` 过滤范围。
        page: 1-based 页码。
        page_size: 每页条数;API 要求 ≥ 10,会自动夹到 10。
        cache_root: 原始 JSON 缓存根目录;``None`` 表示不写。

    Returns:
        envelope dict;404 → ``None``。
    """
    ds = _coerce_date(date_start)
    de = _coerce_date(date_end)
    pncp_cfg = get_pncp_settings()
    effective_ps = max(page_size, MIN_PAGE_SIZE)

    params = {
        "dataInicial": ds.strftime("%Y%m%d"),
        "dataFinal": de.strftime("%Y%m%d"),
        "pagina": page,
        "tamanhoPagina": effective_ps,
    }

    log = logger.bind(
        endpoint="contratos",
        date_start=ds.isoformat(),
        date_end=de.isoformat(),
        page=page,
        page_size=effective_ps,
    )
    log.info("fetcher.contratos.start")

    try:
        data = await session.get_json(_endpoint_url(pncp_cfg), params=params)
    except HttpError as exc:
        if exc.status_code == 404:
            log.warning("fetcher.contratos.not_found")
            return None
        raise

    if data is None:
        log.warning("fetcher.contratos.no_content")
        return None

    if cache_root is not None:
        cached_at = _write_cache(cache_root, ds, page, data)
        log.bind(cache=str(cached_at)).debug("fetcher.contratos.cached")

    log.bind(
        total_registros=data.get("totalRegistros"),
        total_paginas=data.get("totalPaginas"),
        records_in_page=len(data.get("data") or []),
    ).info("fetcher.contratos.done")
    return data


async def fetch_contratos_all(
    date_start: date | str,
    date_end: date | str,
    page_size: int | None = None,
    cache_root: Path | None = None,
) -> AsyncIterator[dict]:
    """遍历所有分页,yield 每条原始 record。

    Args:
        date_start: 公布日期起始(含)。
        date_end: 公布日期截止(含)。
        page_size: 每页条数;``None`` 时用 ``pncp.page_size``,自动夹到 ≥10。
        cache_root: 原始 JSON 缓存根目录。

    Yields:
        原始 record dict(对应 API 返回的 ``data[i]``,不做任何转换)。

    Notes:
        合同接口无 modalidade,所以也无 modalidade 循环 — 单页直进直退。
    """
    pncp_cfg = get_pncp_settings()
    ps = page_size if page_size is not None else int(pncp_cfg["page_size"])
    ps = max(ps, MIN_PAGE_SIZE)

    logger.bind(
        date_start=str(date_start),
        date_end=str(date_end),
        page_size=ps,
    ).info("fetcher.contratos.run")

    async with PncpSession(
        rate_limit_per_second=float(pncp_cfg["rate_limit_per_sec"]),
    ) as session:
        page = 1
        while True:
            resp = await fetch_contratos(
                session,
                date_start,
                date_end,
                page=page,
                page_size=ps,
                cache_root=cache_root,
            )
            if resp is None:
                break

            records = resp.get("data") or []
            if not records:
                logger.bind(page=page).warning("fetcher.contratos.empty_page")
                break

            for rec in records:
                yield rec

            total_pages = int(resp.get("totalPaginas") or 1)
            if page >= total_pages:
                break
            page += 1


__all__ = ["fetch_contratos", "fetch_contratos_all", "ENDPOINT", "MIN_PAGE_SIZE"]
