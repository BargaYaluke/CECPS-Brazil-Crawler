"""PNCP ``/api/consulta/v1/contratacoes/proposta`` fetcher。

跟 :mod:`pncp_publicacao` / :mod:`pncp_atualizacao` 是兄弟接口,差别:

* URL 路径:``/contratacoes/proposta``
* **服务端语义(实测)**:返回"投标截止日 ≤ ``dataFinal`` 的招标"
  — 不是直觉的"≥",**dataFinal 是截止日的上限**,
  反映 PNCP 命名上"proposta"(提案)阶段对应的招标。
  docs/01 把它描述为"投标期内",但 docs 跟实际行为略有出入,以本实测为准。
* 参数只有**一个日期** ``dataFinal``(没有 dataInicial)
* 返回的 record schema 完全跟 publicacao 一致,直接复用 :class:`PublicacaoRaw`

实测 ``codigoModalidadeContratacao`` 跟前面接口一样是**必填**,
``pagina`` / ``tamanhoPagina`` 也都必填(空请求 400)。

典型用法:传一个未来的 ``dataFinal``,拿到截止日在那之前的所有招标
(包括已截止但 PNCP 状态还没刷新的)。业务方再用 record 内的
``dataEncerramentoProposta``(精确到时分秒)做二次过滤。
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

ENDPOINT = "/contratacoes/proposta"
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
    cache_root: Path, data_final: date, modalidade: int, page: int
) -> Path:
    """``<cache_root>/proposta/<YYYY-MM-DD>/modalidade_<n>_page_<m>.json``。

    日期目录用 ``data_final``(因为这是 proposta 唯一的日期参数,
    最能代表"这次查询是看哪一天还能投的")。
    """
    return (
        cache_root
        / "proposta"
        / data_final.isoformat()
        / f"modalidade_{modalidade}_page_{page}.json"
    )


def _write_cache(
    cache_root: Path, data_final: date, modalidade: int, page: int, data: dict
) -> Path:
    target = _cache_path(cache_root, data_final, modalidade, page)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


async def fetch_proposta(
    session: PncpSession,
    data_final: date | str,
    modalidade_code: int,
    page: int = 1,
    page_size: int = 50,
    cache_root: Path | None = None,
) -> dict | None:
    """单页拉取 ``/contratacoes/proposta``(走浏览器内 fetch 解 F5)。

    Args:
        session: 已 ``async with`` 进入的 :class:`PncpSession`。
        data_final: 投标截止日参数;API 返回"截止日 ≥ data_final"的招标。
        modalidade_code: ``codigoModalidadeContratacao``(必填)。
        page: 1-based 页码。
        page_size: 每页条数。
        cache_root: 原始 JSON 缓存根目录;``None`` 表示不写。

    Returns:
        envelope dict;404 → ``None``。
    """
    df = _coerce_date(data_final)
    pncp_cfg = get_pncp_settings()
    page_size = min(_MAX_PAGE_SIZE, max(_MIN_PAGE_SIZE, page_size))  # 夹到 PNCP 允许区间

    params = {
        "dataFinal": df.strftime("%Y%m%d"),
        "codigoModalidadeContratacao": modalidade_code,
        "pagina": page,
        "tamanhoPagina": page_size,
    }

    log = logger.bind(
        endpoint="proposta",
        modalidade=modalidade_code,
        data_final=df.isoformat(),
        page=page,
        page_size=page_size,
    )
    log.info("fetcher.proposta.start")

    try:
        data = await session.get_json(_endpoint_url(pncp_cfg), params=params)
    except HttpError as exc:
        if exc.status_code == 404:
            log.warning("fetcher.proposta.not_found")
            return None
        raise

    if data is None:
        log.warning("fetcher.proposta.no_content")
        return None

    if cache_root is not None:
        cached_at = _write_cache(cache_root, df, modalidade_code, page, data)
        log.bind(cache=str(cached_at)).debug("fetcher.proposta.cached")

    log.bind(
        total_registros=data.get("totalRegistros"),
        total_paginas=data.get("totalPaginas"),
        records_in_page=len(data.get("data") or []),
    ).info("fetcher.proposta.done")
    return data


async def fetch_proposta_all(
    data_final: date | str,
    modalidade_codes: list[int] | None = None,
    page_size: int | None = None,
    cache_root: Path | None = None,
) -> AsyncIterator[dict]:
    """遍历所有 modalidade × 分页,yield 每条原始 record。

    Args:
        data_final: 投标截止日参数(单日,无 dataInicial)。
        modalidade_codes: modalidade 列表;``None`` 用 ``modalidades.yaml`` 的 default_iter。
        page_size: 每页条数;``None`` 时用 ``pncp.page_size``。
        cache_root: 原始 JSON 缓存根目录。

    Yields:
        原始 record dict。

    Notes:
        404 / 空响应只记 warning,跳过当前 modalidade 继续下一个
        (CLAUDE.md §7)。
    """
    pncp_cfg = get_pncp_settings()
    codes = list(modalidade_codes) if modalidade_codes else get_default_modalidade_iter()
    ps = page_size if page_size is not None else int(pncp_cfg["page_size"])

    if not codes:
        logger.warning("fetcher.proposta.no_modalidades")
        return

    logger.bind(
        modalidades=codes,
        data_final=str(data_final),
        page_size=ps,
    ).info("fetcher.proposta.run")

    async with PncpSession(
        rate_limit_per_second=float(pncp_cfg["rate_limit_per_sec"]),
    ) as session:
        for code in codes:
            page = 1
            while True:
                resp = await fetch_proposta(
                    session,
                    data_final,
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
                        "fetcher.proposta.empty_page"
                    )
                    break

                for rec in records:
                    yield rec

                total_pages = int(resp.get("totalPaginas") or 1)
                if page >= total_pages:
                    break
                page += 1


__all__ = ["fetch_proposta", "fetch_proposta_all", "ENDPOINT"]
