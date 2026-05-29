"""tests/unit/test_compras_gov_catalogo.py — Compras.gov.br 目录 fetcher 单测。

用 ``pytest_httpx`` mock httpx 响应,验证:分页参数拼接、tamanhoPagina 夹取、
多页遍历、空页终止、material/servico 入口端点。
"""
from __future__ import annotations

import re

from pytest_httpx import HTTPXMock

from src.core.http_client import HttpClient
from src.fetchers.compras_gov_catalogo import (
    MATERIAL_ENDPOINT,
    SERVICO_ENDPOINT,
    fetch_catalogo_all,
    fetch_catalogo_page,
    fetch_material_all,
    fetch_servico_all,
)

BASE = "https://dadosabertos.compras.gov.br"
MATERIAL_URL = re.compile(
    r"https://dadosabertos\.compras\.gov\.br/modulo-material/4_consultarItemMaterial.*"
)
SERVICO_URL = re.compile(
    r"https://dadosabertos\.compras\.gov\.br/modulo-servico/6_consultarItemServico.*"
)


def _envelope(records: list[dict], total_paginas: int) -> dict:
    """构造该 API 的标准返回包。"""
    return {
        "resultado": records,
        "totalRegistros": 999,
        "totalPaginas": total_paginas,
        "paginasRestantes": max(0, total_paginas - 1),
    }


async def test_page_builds_params_and_returns_envelope(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=MATERIAL_URL, json=_envelope([{"codigoItem": 1}], 1))

    async with HttpClient(base_url=BASE, rate_limit_per_second=0) as client:
        env = await fetch_catalogo_page(client, MATERIAL_ENDPOINT, page=1, page_size=10)

    assert env is not None
    assert env["resultado"] == [{"codigoItem": 1}]
    req = httpx_mock.get_requests()[0]
    assert "pagina=1" in str(req.url)
    assert "tamanhoPagina=10" in str(req.url)


async def test_page_size_clamped_to_min_10(httpx_mock: HTTPXMock) -> None:
    """传 page_size=2(小于 API 下限)应被夹到 10。"""
    httpx_mock.add_response(url=MATERIAL_URL, json=_envelope([], 1))

    async with HttpClient(base_url=BASE, rate_limit_per_second=0) as client:
        records = [
            r
            async for r in fetch_catalogo_all(
                MATERIAL_ENDPOINT, modulo="material", page_size=2, client=client
            )
        ]

    assert records == []
    req = httpx_mock.get_requests()[0]
    assert "tamanhoPagina=10" in str(req.url)


async def test_page_size_clamped_to_max_500(httpx_mock: HTTPXMock) -> None:
    """传 page_size=9999(大于 API 上限)应被夹到 500。"""
    httpx_mock.add_response(url=MATERIAL_URL, json=_envelope([], 1))

    async with HttpClient(base_url=BASE, rate_limit_per_second=0) as client:
        _ = [
            r
            async for r in fetch_catalogo_all(
                MATERIAL_ENDPOINT, modulo="material", page_size=9999, client=client
            )
        ]

    req = httpx_mock.get_requests()[0]
    assert "tamanhoPagina=500" in str(req.url)


async def test_fetch_all_paginates_across_pages(httpx_mock: HTTPXMock) -> None:
    """totalPaginas=2 → 抓两页,合并所有 resultado 记录。"""
    httpx_mock.add_response(
        url=MATERIAL_URL, json=_envelope([{"codigoItem": 1}, {"codigoItem": 2}], 2)
    )
    httpx_mock.add_response(url=MATERIAL_URL, json=_envelope([{"codigoItem": 3}], 2))

    async with HttpClient(base_url=BASE, rate_limit_per_second=0) as client:
        records = [
            r
            async for r in fetch_catalogo_all(
                MATERIAL_ENDPOINT, modulo="material", page_size=10, client=client
            )
        ]

    assert [r["codigoItem"] for r in records] == [1, 2, 3]
    assert len(httpx_mock.get_requests()) == 2


async def test_fetch_all_stops_on_empty_resultado(httpx_mock: HTTPXMock) -> None:
    """首页 resultado 为空 → 立刻停,不再翻页。"""
    httpx_mock.add_response(url=MATERIAL_URL, json=_envelope([], 5))

    async with HttpClient(base_url=BASE, rate_limit_per_second=0) as client:
        records = [
            r
            async for r in fetch_catalogo_all(
                MATERIAL_ENDPOINT, modulo="material", page_size=10, client=client
            )
        ]

    assert records == []
    assert len(httpx_mock.get_requests()) == 1


async def test_fetch_material_all_hits_material_endpoint(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=MATERIAL_URL, json=_envelope([{"codigoItem": 206504}], 1))

    async with HttpClient(base_url=BASE, rate_limit_per_second=0) as client:
        records = [r async for r in fetch_material_all(page_size=10, client=client)]

    assert records == [{"codigoItem": 206504}]
    assert "/modulo-material/4_consultarItemMaterial" in str(httpx_mock.get_requests()[0].url)


async def test_fetch_servico_all_hits_servico_endpoint(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=SERVICO_URL, json=_envelope([{"codigoServico": 12345}], 1))

    async with HttpClient(base_url=BASE, rate_limit_per_second=0) as client:
        records = [r async for r in fetch_servico_all(page_size=10, client=client)]

    assert records == [{"codigoServico": 12345}]
    assert "/modulo-servico/6_consultarItemServico" in str(httpx_mock.get_requests()[0].url)
