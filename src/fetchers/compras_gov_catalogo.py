"""Compras.gov.br(dadosabertos)目录采集 fetcher。

数据源: ``https://dadosabertos.compras.gov.br`` —— 联邦采购 SIASG 体系的开放数据 API,
**免认证、非 F5**,所以走普通 :class:`HttpClient`,不需要 BrowserSession。

本模块负责拉两类标准品类目录:

* CATMAT(物料): ``/modulo-material/4_consultarItemMaterial``
* CATSER(服务): ``/modulo-servico/6_consultarItemServico``

二者返回包形态一致(本 API 几乎所有 ``consultar*`` 端点都如此),故抽象出通用分页器::

    {
      "resultado": [ {...}, {...} ],
      "totalRegistros": 342120,
      "totalPaginas": 685,
      "paginasRestantes": 684
    }

只负责拉数据 + 把原始 JSON 落到 ``data/raw/compras_catalogo/`` —— 不做富化 / 入库 / 分类,
那是 enrichers / storage / classifiers 层的事(见 CLAUDE.md §1)。

实测要点(2026-05-28 真探 API):
    * ``tamanhoPagina`` 取值区间 **10~500**(传 <10 会 400 ``Informe um número de
      paginação no intervalo de 10 a 500``)—— 与 PNCP contratos/pca 同坑,本模块强制夹取。
    * 分页参数是 ``pagina`` / ``tamanhoPagina``(与 PNCP 一致)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

from ..core.http_client import HttpClient
from ..core.logger import logger
from ..core.settings import get_compras_settings

# ─── 端点常量 ──────────────────────────────────────────────────────────────
MATERIAL_ENDPOINT = "/modulo-material/4_consultarItemMaterial"
SERVICO_ENDPOINT = "/modulo-servico/6_consultarItemServico"

# 本 API 业务校验:每页条数必须在 [10, 500]
_MIN_PAGE_SIZE = 10
_MAX_PAGE_SIZE = 500


def _clamp_page_size(page_size: int) -> int:
    """把每页条数夹到 API 允许的 ``[10, 500]`` 区间。"""
    return max(_MIN_PAGE_SIZE, min(_MAX_PAGE_SIZE, int(page_size)))


def _base_url() -> str:
    """从 ``settings.yaml`` 的 ``compras_gov.base_url`` 读取(去尾斜杠)。"""
    return str(get_compras_settings()["base_url"]).rstrip("/")


def _endpoint_url(endpoint: str, base_url: str | None = None) -> str:
    """拼绝对 URL(传绝对 URL 给 HttpClient,不依赖 client 自身 base_url)。"""
    base = (base_url if base_url is not None else _base_url()).rstrip("/")
    return f"{base}{endpoint}"


def _cache_path(cache_root: Path, modulo: str, page: int) -> Path:
    """``<cache_root>/compras_catalogo/<modulo>/page_<n>.json``。"""
    return cache_root / "compras_catalogo" / modulo / f"page_{page}.json"


def _write_cache(cache_root: Path, modulo: str, page: int, data: dict) -> Path:
    """把原始响应写入缓存目录(覆盖式)。"""
    target = _cache_path(cache_root, modulo, page)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


async def fetch_catalogo_page(
    client: HttpClient,
    endpoint: str,
    *,
    page: int,
    page_size: int,
    extra_params: dict[str, Any] | None = None,
    base_url: str | None = None,
    cache_root: Path | None = None,
    modulo: str = "catalogo",
) -> dict[str, Any] | None:
    """拉取某个 ``consultar*`` 端点的单页,返回整个响应包(envelope)。

    Args:
        client: 已就绪的 :class:`HttpClient`。
        endpoint: 端点路径(如 :data:`MATERIAL_ENDPOINT`)。
        page: 页码,从 1 开始。
        page_size: 每页条数(内部夹取到 ``[10, 500]``)。
        extra_params: 额外查询参数(过滤条件,如 ``codigoClasse``)。
        base_url: 覆盖默认 base_url(单测/特例用)。
        cache_root: 原始 JSON 缓存根目录;``None`` 表示不写缓存。
        modulo: 缓存子目录名(``material`` / ``servico`` 等)。

    Returns:
        响应包 dict(``{resultado, totalRegistros, totalPaginas, paginasRestantes}``);
        空响应 / 非 JSON 返回 ``None``,**不抛出**。

    Raises:
        HttpError: 4xx / 5xx / 网络错(由 HttpClient 抛出)。
    """
    ps = _clamp_page_size(page_size)
    url = _endpoint_url(endpoint, base_url)
    params: dict[str, Any] = {"pagina": page, "tamanhoPagina": ps}
    if extra_params:
        params.update(extra_params)

    log = logger.bind(source="compras_gov", modulo=modulo, endpoint=endpoint, page=page, page_size=ps)
    log.info("fetcher.compras_catalogo.page.start")

    data = await client.get_json(url, params=params)
    if data is None:
        log.warning("fetcher.compras_catalogo.page.no_content")
        return None
    if not isinstance(data, dict):
        log.bind(actual_type=type(data).__name__).warning(
            "fetcher.compras_catalogo.page.unexpected_shape"
        )
        return None

    if cache_root is not None:
        cached_at = _write_cache(cache_root, modulo, page, data)
        log.bind(cache=str(cached_at)).debug("fetcher.compras_catalogo.page.cached")

    log.bind(
        total_registros=data.get("totalRegistros"),
        total_paginas=data.get("totalPaginas"),
        records_in_page=len(data.get("resultado") or []),
    ).info("fetcher.compras_catalogo.page.done")
    return data


async def _iter_pages(
    client: HttpClient,
    endpoint: str,
    *,
    modulo: str,
    page_size: int,
    extra_params: dict[str, Any] | None,
    base_url: str | None,
    cache_root: Path | None,
) -> AsyncIterator[dict[str, Any]]:
    """用给定 client 遍历所有分页,yield ``resultado`` 里每条记录。"""
    page = 1
    while True:
        envelope = await fetch_catalogo_page(
            client,
            endpoint,
            page=page,
            page_size=page_size,
            extra_params=extra_params,
            base_url=base_url,
            cache_root=cache_root,
            modulo=modulo,
        )
        if envelope is None:
            break
        records = envelope.get("resultado") or []
        if not records:
            logger.bind(source="compras_gov", modulo=modulo, page=page).warning(
                "fetcher.compras_catalogo.empty_page"
            )
            break
        for rec in records:
            yield rec

        total_pages = int(envelope.get("totalPaginas") or 1)
        if page >= total_pages:
            break
        page += 1


async def fetch_catalogo_all(
    endpoint: str,
    *,
    modulo: str,
    page_size: int | None = None,
    extra_params: dict[str, Any] | None = None,
    cache_root: Path | None = None,
    client: HttpClient | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """遍历某个 ``consultar*`` 端点的所有分页,yield 每条原始记录。

    Args:
        endpoint: 端点路径(如 :data:`MATERIAL_ENDPOINT`)。
        modulo: 缓存/日志用的模块名(``material`` / ``servico``)。
        page_size: 每页条数;``None`` 时用 ``compras_gov.page_size``(默认 500)。内部夹到 ``[10,500]``。
        extra_params: 额外查询参数(过滤条件)。
        cache_root: 原始 JSON 缓存根目录;``None`` 表示不写缓存。
        client: 已就绪的 :class:`HttpClient`(共享时传入);``None`` 时本函数内建临时实例。

    Yields:
        原始 record dict(对应 ``resultado[i]``,不做任何字段转换)。

    Raises:
        HttpError: 非首页之外的 HTTP 错误会向上抛(由 HttpClient 抛出)。
    """
    cfg = get_compras_settings()
    ps = _clamp_page_size(page_size if page_size is not None else int(cfg["page_size"]))
    base_url = str(cfg["base_url"]).rstrip("/")

    logger.bind(source="compras_gov", modulo=modulo, endpoint=endpoint, page_size=ps).info(
        "fetcher.compras_catalogo.run"
    )

    if client is not None:
        async for rec in _iter_pages(
            client,
            endpoint,
            modulo=modulo,
            page_size=ps,
            extra_params=extra_params,
            base_url=base_url,
            cache_root=cache_root,
        ):
            yield rec
    else:
        async with HttpClient(
            base_url=base_url,
            timeout=float(cfg["timeout"]),
            rate_limit_per_second=float(cfg["rate_limit_per_sec"]),
        ) as c:
            async for rec in _iter_pages(
                c,
                endpoint,
                modulo=modulo,
                page_size=ps,
                extra_params=extra_params,
                base_url=base_url,
                cache_root=cache_root,
            ):
                yield rec


def fetch_material_all(
    *,
    page_size: int | None = None,
    extra_params: dict[str, Any] | None = None,
    cache_root: Path | None = None,
    client: HttpClient | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """遍历 CATMAT 物料目录(``/modulo-material/4_consultarItemMaterial``)。"""
    return fetch_catalogo_all(
        MATERIAL_ENDPOINT,
        modulo="material",
        page_size=page_size,
        extra_params=extra_params,
        cache_root=cache_root,
        client=client,
    )


def fetch_servico_all(
    *,
    page_size: int | None = None,
    extra_params: dict[str, Any] | None = None,
    cache_root: Path | None = None,
    client: HttpClient | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """遍历 CATSER 服务目录(``/modulo-servico/6_consultarItemServico``)。"""
    return fetch_catalogo_all(
        SERVICO_ENDPOINT,
        modulo="servico",
        page_size=page_size,
        extra_params=extra_params,
        cache_root=cache_root,
        client=client,
    )


__all__ = [
    "MATERIAL_ENDPOINT",
    "SERVICO_ENDPOINT",
    "fetch_catalogo_page",
    "fetch_catalogo_all",
    "fetch_material_all",
    "fetch_servico_all",
]
