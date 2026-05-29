"""PNCP ``/api/consulta/v1/contratacoes/publicacao`` fetcher。

只负责拉数据 + 把原始 JSON 落到 ``data/raw/publicacao/``。
不做过滤 / 富化 / 入库 — 那是后续层的事。

参考: docs/01_爬取来源设计.md 第 1.1 节
Swagger: https://pncp.gov.br/api/consulta/swagger-ui/index.html

实现说明
--------
PNCP 的 ``/api/consulta/v1/*`` 路径**曾**装着 F5 BIG-IP ASM(cookies 绑 TLS 指纹,
httpx 重放无效),所以历史上全程走真浏览器。2026-05-28 实测 F5 撤了,改用
:class:`src.fetchers.pncp_session.PncpSession` 统一传输:默认 ``auto`` 模式 httpx
优先(快 5-10x),撞 F5 自动 fallback 真浏览器。传输方式由 ``pncp.transport`` 配置。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import AsyncIterator

from ..core.exceptions import HttpError
from ..core.logger import logger
from ..core.settings import get_default_modalidade_iter, get_pncp_settings
from .pncp_session import PncpSession

ENDPOINT = "/contratacoes/publicacao"
# PNCP /contratacoes/* 的 tamanhoPagina 实测区间 [10, 50];>50 报 400「Tamanho de página inválido」
_MIN_PAGE_SIZE = 10
_MAX_PAGE_SIZE = 50


def _coerce_date(value: date | str) -> date:
    """``date`` 或 ``YYYY-MM-DD`` 字符串 → ``date``。"""
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _endpoint_url(pncp_cfg: dict) -> str:
    """拼绝对 URL(底层 get_json 不依赖 client base_url)。"""
    base = pncp_cfg["base_url"].rstrip("/")
    return f"{base}{ENDPOINT}"


def _cache_path(cache_root: Path, date_start: date, modalidade: int, page: int) -> Path:
    """生成缓存文件路径: ``<cache_root>/publicacao/<YYYY-MM-DD>/modalidade_<n>_page_<m>.json``。"""
    return (
        cache_root
        / "publicacao"
        / date_start.isoformat()
        / f"modalidade_{modalidade}_page_{page}.json"
    )


def _write_cache(
    cache_root: Path, date_start: date, modalidade: int, page: int, data: dict
) -> Path:
    """把原始响应写入缓存目录(覆盖式)。"""
    target = _cache_path(cache_root, date_start, modalidade, page)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


async def fetch_publicacao(
    session: PncpSession,
    date_start: date | str,
    date_end: date | str,
    modalidade_code: int,
    page: int = 1,
    page_size: int = 50,
    cache_root: Path | None = None,
) -> dict | None:
    """单页拉取 ``/contratacoes/publicacao``(走浏览器内 fetch)。

    Args:
        session: 已 ``async with`` 进入的 :class:`PncpSession`。
        date_start: 起始发布日期(date 或 ``YYYY-MM-DD``)。
        date_end: 截止发布日期。
        modalidade_code: ``codigoModalidadeContratacao``(见 ``modalidades.yaml``)。
        page: 页码,从 1 开始。
        page_size: 每页条数(PNCP API 业务校验要求 ≥ 10)。
        cache_root: 原始 JSON 缓存根目录(``./data/raw`` 风格);``None`` 表示不写缓存。

    Returns:
        解析后的响应 dict;若收到 404,记 warning 并返回 ``None``,**不抛出**。

    Raises:
        HttpError: 非 404 的 HTTP 错误。
    """
    ds = _coerce_date(date_start)
    de = _coerce_date(date_end)
    pncp_cfg = get_pncp_settings()
    page_size = min(_MAX_PAGE_SIZE, max(_MIN_PAGE_SIZE, page_size))  # 夹到 PNCP 允许区间

    params = {
        "dataInicial": ds.strftime("%Y%m%d"),
        "dataFinal": de.strftime("%Y%m%d"),
        "codigoModalidadeContratacao": modalidade_code,
        "pagina": page,
        "tamanhoPagina": page_size,
    }

    log = logger.bind(
        endpoint="publicacao",
        modalidade=modalidade_code,
        date_start=ds.isoformat(),
        date_end=de.isoformat(),
        page=page,
        page_size=page_size,
    )
    log.info("fetcher.publicacao.start")

    try:
        data = await session.get_json(_endpoint_url(pncp_cfg), params=params)
    except HttpError as exc:
        if exc.status_code == 404:
            log.warning("fetcher.publicacao.not_found")
            return None
        raise

    if data is None:
        # PNCP 在无结果时常返回 204 No Content / 空 body
        log.warning("fetcher.publicacao.no_content")
        return None

    if cache_root is not None:
        cached_at = _write_cache(cache_root, ds, modalidade_code, page, data)
        log.bind(cache=str(cached_at)).debug("fetcher.publicacao.cached")

    log.bind(
        total_registros=data.get("totalRegistros"),
        total_paginas=data.get("totalPaginas"),
        records_in_page=len(data.get("data") or []),
    ).info("fetcher.publicacao.done")

    return data


async def fetch_publicacao_all(
    date_start: date | str,
    date_end: date | str,
    modalidade_codes: list[int] | None = None,
    page_size: int | None = None,
    cache_root: Path | None = None,
) -> AsyncIterator[dict]:
    """遍历所有 modalidade 和分页,yield 每条原始记录。

    Args:
        date_start: 起始发布日期。
        date_end: 截止发布日期。
        modalidade_codes: 要遍历的 modalidade 列表;``None`` 时用 ``modalidades.yaml`` 的 ``default_iter``。
        page_size: 每页条数;``None`` 时用 ``pncp.page_size``。
        cache_root: 原始 JSON 缓存根目录;``None`` 表示不写缓存。

    Yields:
        原始 record dict(对应 API 返回的 ``data[i]``,不做任何字段转换)。

    Notes:
        404 / 空响应只记 warning,流程继续到下一个 modalidade(见 CLAUDE.md §7)。
    """
    pncp_cfg = get_pncp_settings()
    codes = list(modalidade_codes) if modalidade_codes else get_default_modalidade_iter()
    ps = page_size if page_size is not None else int(pncp_cfg["page_size"])

    if not codes:
        logger.warning("fetcher.publicacao.no_modalidades")
        return

    logger.bind(
        modalidades=codes,
        date_start=str(date_start),
        date_end=str(date_end),
        page_size=ps,
    ).info("fetcher.publicacao.run")

    async with PncpSession(
        rate_limit_per_second=float(pncp_cfg["rate_limit_per_sec"]),
    ) as session:
        for code in codes:
            page = 1
            while True:
                resp = await fetch_publicacao(
                    session,
                    date_start,
                    date_end,
                    code,
                    page=page,
                    page_size=ps,
                    cache_root=cache_root,
                )
                if resp is None:
                    # 404 / 空响应 — 跳到下一个 modalidade
                    break

                records = resp.get("data") or []
                if not records:
                    logger.bind(modalidade=code, page=page).warning(
                        "fetcher.publicacao.empty_page"
                    )
                    break

                for rec in records:
                    yield rec

                total_pages = int(resp.get("totalPaginas") or 1)
                if page >= total_pages:
                    break
                page += 1


__all__ = ["fetch_publicacao", "fetch_publicacao_all", "ENDPOINT"]
