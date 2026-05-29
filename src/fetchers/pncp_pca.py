"""PNCP ``/api/consulta/v1/pca/atualizacao`` fetcher。

PCA = Plano de Contratações Anual = 年度采购计划。
业务价值(docs/01 §1.3):**提前 6-12 个月预知商机** — 每个机构每年初发布
次年的采购计划。

**实测路径**(2026-05-28 探测):
* ``/v1/pca/`` — 需 ``codigoClassificacaoSuperior`` 必填(分类码) → 不通用
* ``/v1/pca/atualizacao`` — 只需 ``dataInicio`` + ``dataFim`` + ``pagina`` → **本 fetcher 用这个**
* ``/v1/pca/usuario`` — 按 ``idUsuario`` 查

**数据结构特殊**(跟招标 / 合同都不同):
每条 record 是一个 "PCA 头 + N 个嵌套 itens" 的复合对象。
fetcher 原样返回 record;入库时 :meth:`PcaRaw.explode_items` 把头部扁平到每个 item。

**反爬状态**(2026-05-28):F5 WAF 暂时撤了,可以直接用 ``HttpClient``
(`/api/consulta/v1/pca/*` 实测 200 JSON)。如果 WAF 回来,把
``HttpClient`` 换成 ``BrowserSession`` 即可。

**数据规模警告**:7 天范围 PCA 共 113 万条,跨年(2027 计划)— 比合同体量
大 23 倍,记得用 ``--limit`` 控制。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import AsyncIterator

from ..core.exceptions import HttpError
from ..core.http_client import HttpClient
from ..core.logger import logger
from ..core.settings import get_pncp_settings

ENDPOINT = "/pca/atualizacao"
MIN_PAGE_SIZE = 10  # 跟 contratos 一样,API 强制


def _coerce_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _endpoint_url(pncp_cfg: dict) -> str:
    base = pncp_cfg["base_url"].rstrip("/")
    return f"{base}{ENDPOINT}"


def _cache_path(cache_root: Path, date_start: date, page: int) -> Path:
    """``<cache_root>/pca/<YYYY-MM-DD>/page_<n>.json``。"""
    return cache_root / "pca" / date_start.isoformat() / f"page_{page}.json"


def _write_cache(cache_root: Path, date_start: date, page: int, data: dict) -> Path:
    target = _cache_path(cache_root, date_start, page)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


async def fetch_pca(
    client: HttpClient,
    date_start: date | str,
    date_end: date | str,
    page: int = 1,
    page_size: int = 50,
    cache_root: Path | None = None,
) -> dict | None:
    """单页拉取 ``/pca/atualizacao``(普通 HttpClient,无 F5 挑战)。

    Args:
        client: 已 ``async with`` 进入的 :class:`HttpClient`。
        date_start / date_end: ``dataAtualizacaoGlobalPCA`` 过滤范围(API 字段名是 ``dataInicio`` / ``dataFim``)。
        page: 1-based 页码。
        page_size: 每页条数;API 强制 ≥ 10,自动夹。
        cache_root: 原始 JSON 缓存根目录;``None`` 表示不写。

    Returns:
        envelope dict;404 → ``None``。
    """
    ds = _coerce_date(date_start)
    de = _coerce_date(date_end)
    pncp_cfg = get_pncp_settings()
    effective_ps = max(page_size, MIN_PAGE_SIZE)

    params = {
        "dataInicio": ds.strftime("%Y%m%d"),
        "dataFim": de.strftime("%Y%m%d"),
        "pagina": page,
        "tamanhoPagina": effective_ps,
    }

    log = logger.bind(
        endpoint="pca",
        date_start=ds.isoformat(),
        date_end=de.isoformat(),
        page=page,
        page_size=effective_ps,
    )
    log.info("fetcher.pca.start")

    try:
        data = await client.get_json(_endpoint_url(pncp_cfg), params=params)
    except HttpError as exc:
        if exc.status_code == 404:
            log.warning("fetcher.pca.not_found")
            return None
        raise

    if data is None:
        log.warning("fetcher.pca.no_content")
        return None

    if cache_root is not None:
        cached_at = _write_cache(cache_root, ds, page, data)
        log.bind(cache=str(cached_at)).debug("fetcher.pca.cached")

    # 统计 PCA 头部数 + 内嵌 itens 总数(给业务方看真实数据规模)
    records = data.get("data") or []
    n_pcas = len(records)
    n_items_total = sum(len(r.get("itens") or []) for r in records)
    log.bind(
        total_registros=data.get("totalRegistros"),
        total_paginas=data.get("totalPaginas"),
        n_pcas_in_page=n_pcas,
        n_items_in_page=n_items_total,
    ).info("fetcher.pca.done")
    return data


async def fetch_pca_all(
    date_start: date | str,
    date_end: date | str,
    page_size: int | None = None,
    cache_root: Path | None = None,
) -> AsyncIterator[dict]:
    """遍历所有分页,yield **每条 PCA 头**(含嵌套 itens 数组)。

    Args:
        date_start: ``dataAtualizacaoGlobalPCA`` 起始(含)。
        date_end: 截止(含)。
        page_size: 每页条数(自动 ≥ 10)。
        cache_root: 原始 JSON 缓存根目录。

    Yields:
        每条 PCA dict(头 9 字段 + ``itens`` 数组)。
        调用方负责调用 :meth:`PcaRaw.explode_items` 拍扁后入库。
    """
    pncp_cfg = get_pncp_settings()
    ps = max(
        page_size if page_size is not None else int(pncp_cfg["page_size"]),
        MIN_PAGE_SIZE,
    )

    logger.bind(
        date_start=str(date_start),
        date_end=str(date_end),
        page_size=ps,
    ).info("fetcher.pca.run")

    async with HttpClient(
        base_url="",  # PCA fetcher 用绝对 URL,base_url 留空避免 httpx 拼接
        rate_limit_per_second=float(pncp_cfg["rate_limit_per_sec"]),
    ) as client:
        page = 1
        while True:
            resp = await fetch_pca(
                client,
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
                logger.bind(page=page).warning("fetcher.pca.empty_page")
                break

            for rec in records:
                yield rec

            total_pages = int(resp.get("totalPaginas") or 1)
            if page >= total_pages:
                break
            page += 1


__all__ = ["fetch_pca", "fetch_pca_all", "ENDPOINT", "MIN_PAGE_SIZE"]
