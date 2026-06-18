"""PNCP ``/api/consulta/v1/contratacoes/atualizacao`` fetcher。

跟 :mod:`pncp_publicacao` 几乎一模一样,差别只有两点:

* URL 路径:``/contratacoes/atualizacao``
* 服务端过滤条件是 ``dataAtualizacao``(最近被改动的记录),而不是
  ``dataPublicacaoPncp``

**重要**:实测发现 ``codigoModalidadeContratacao`` 跟 publicacao 一样是
**必填**(Swagger 文档不准),所以需要按 modalidade 循环遍历。
返回的 record schema 完全跟 publicacao 一致,直接复用 :class:`PublicacaoRaw`。

用于"增量同步":每天只拉过去 1-7 天**新增 + 改动**的记录。
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

ENDPOINT = "/contratacoes/atualizacao"
# PNCP /contratacoes/* 的 tamanhoPagina 实测区间 [10, 50];>50 报 400「Tamanho de página inválido」
_MIN_PAGE_SIZE = 10
_MAX_PAGE_SIZE = 50


def _coerce_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _endpoint_url(pncp_cfg: dict) -> str:
    base = pncp_cfg["base_url"].rstrip("/")
    return f"{base}{ENDPOINT}"


def _cache_path(
    cache_root: Path, date_start: date, modalidade: int, page: int
) -> Path:
    """``<cache_root>/atualizacao/<YYYY-MM-DD>/modalidade_<n>_page_<m>.json``。"""
    return (
        cache_root
        / "atualizacao"
        / date_start.isoformat()
        / f"modalidade_{modalidade}_page_{page}.json"
    )


def _write_cache(
    cache_root: Path, date_start: date, modalidade: int, page: int, data: dict
) -> Path:
    target = _cache_path(cache_root, date_start, modalidade, page)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


async def fetch_atualizacao(
    session: PncpSession,
    date_start: date | str,
    date_end: date | str,
    modalidade_code: int,
    page: int = 1,
    page_size: int = 50,
    cache_root: Path | None = None,
) -> dict | None:
    """单页拉取 ``/contratacoes/atualizacao``(走浏览器内 fetch 解 F5)。

    Args:
        session: 已 ``async with`` 进入的 :class:`PncpSession`。
        date_start / date_end: ``dataAtualizacao`` 过滤范围。
        modalidade_code: ``codigoModalidadeContratacao``(实测必填)。
        page: 1-based 页码。
        page_size: 每页条数(PNCP 上限 500,默认 50)。
        cache_root: 原始 JSON 缓存根目录;``None`` 表示不写。

    Returns:
        envelope dict;404 → ``None``。
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
        endpoint="atualizacao",
        modalidade=modalidade_code,
        date_start=ds.isoformat(),
        date_end=de.isoformat(),
        page=page,
        page_size=page_size,
    )
    log.info("fetcher.atualizacao.start")

    try:
        data = await session.get_json(_endpoint_url(pncp_cfg), params=params)
    except HttpError as exc:
        if exc.status_code == 404:
            log.warning("fetcher.atualizacao.not_found")
            return None
        raise

    if data is None:
        log.warning("fetcher.atualizacao.no_content")
        return None

    if cache_root is not None:
        cached_at = _write_cache(cache_root, ds, modalidade_code, page, data)
        log.bind(cache=str(cached_at)).debug("fetcher.atualizacao.cached")

    log.bind(
        total_registros=data.get("totalRegistros"),
        total_paginas=data.get("totalPaginas"),
        records_in_page=len(data.get("data") or []),
    ).info("fetcher.atualizacao.done")
    return data


async def fetch_atualizacao_all(
    date_start: date | str,
    date_end: date | str,
    modalidade_codes: list[int] | None = None,
    page_size: int | None = None,
    cache_root: Path | None = None,
) -> AsyncIterator[dict]:
    """遍历所有 modalidade × 分页,yield 每条原始 record。

    Args:
        date_start: ``dataAtualizacao`` 起始(含)。
        date_end: ``dataAtualizacao`` 截止(含)。
        modalidade_codes: modalidade 列表;``None`` 用 ``modalidades.yaml`` 的 default_iter。
        page_size: 每页条数;``None`` 时用 ``pncp.page_size``。
        cache_root: 原始 JSON 缓存根目录。

    Yields:
        原始 record dict。

    Notes:
        404 / 空响应只记 warning,跳过当前 modalidade 继续下一个。
    """
    pncp_cfg = get_pncp_settings()
    codes = list(modalidade_codes) if modalidade_codes else get_default_modalidade_iter()
    ps = page_size if page_size is not None else int(pncp_cfg["page_size"])

    if not codes:
        logger.warning("fetcher.atualizacao.no_modalidades")
        return

    logger.bind(
        modalidades=codes,
        date_start=str(date_start),
        date_end=str(date_end),
        page_size=ps,
    ).info("fetcher.atualizacao.run")

    async with PncpSession(
        rate_limit_per_second=float(pncp_cfg["rate_limit_per_sec"]),
    ) as session:
        for code in codes:
            page = 1
            while True:
                resp = await fetch_atualizacao(
                    session,
                    date_start,
                    date_end,
                    code,
                    page=page,
                    page_size=ps,
                    cache_root=cache_root,
                )
                if resp is None:
                    break

                records = resp.get("data") or []
                if not records:
                    logger.bind(modalidade=code, page=page).warning(
                        "fetcher.atualizacao.empty_page"
                    )
                    break

                for rec in records:
                    yield rec

                total_pages = int(resp.get("totalPaginas") or 1)
                if page >= total_pages:
                    break
                page += 1


__all__ = ["fetch_atualizacao", "fetch_atualizacao_all", "ENDPOINT"]
