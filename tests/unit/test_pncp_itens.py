"""tests/unit/test_pncp_itens.py — itens fetcher 单测。

用 ``pytest_httpx`` mock httpx 响应,验证 URL 拼接 / 返回类型处理。
"""
from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

from src.core.http_client import HttpClient
from src.fetchers.pncp_itens import fetch_itens

ITENS_URL_PATTERN = re.compile(
    r"https://pncp\.gov\.br/api/pncp/v1/orgaos/\d{14}/compras/\d{4}/\d+/itens"
)


@pytest.fixture
def sample_items() -> list[dict]:
    """3 条最小 item 样本(字段够触发 ItemRaw 校验即可)。"""
    return [
        {"numeroItem": 1, "descricao": "服务A", "valorTotal": 100.0},
        {"numeroItem": 2, "descricao": "材料B", "valorTotal": 50.0},
        {"numeroItem": 3, "descricao": "服务C", "valorTotal": 200.0},
    ]


async def test_fetch_itens_returns_list(httpx_mock: HTTPXMock, sample_items: list[dict]) -> None:
    httpx_mock.add_response(url=ITENS_URL_PATTERN, json=sample_items)

    async with HttpClient(rate_limit_per_second=0) as client:
        result = await fetch_itens("01612441000107", 2026, 76, client=client)

    assert result == sample_items
    assert len(result) == 3
    req = httpx_mock.get_requests()[0]
    assert "/orgaos/01612441000107/compras/2026/76/itens" in str(req.url)


async def test_fetch_itens_404_raises_http_error(httpx_mock: HTTPXMock) -> None:
    """404 走 HttpClient 的 4xx 分支,直接抛 HttpError(orchestrator 捕获)。"""
    from src.core.exceptions import HttpError

    httpx_mock.add_response(url=ITENS_URL_PATTERN, status_code=404)

    async with HttpClient(rate_limit_per_second=0) as client:
        with pytest.raises(HttpError):
            await fetch_itens("11111111000111", 2026, 999, client=client)


async def test_fetch_itens_empty_body_returns_empty_list(httpx_mock: HTTPXMock) -> None:
    """204 No Content → fetch_itens 返回 ``[]``。"""
    httpx_mock.add_response(url=ITENS_URL_PATTERN, status_code=204, content=b"")

    async with HttpClient(rate_limit_per_second=0) as client:
        result = await fetch_itens("01612441000107", 2026, 76, client=client)

    assert result == []


async def test_fetch_itens_non_list_response_returns_empty(httpx_mock: HTTPXMock) -> None:
    """API 罕见返回 dict / null → 不抛错,返回 ``[]``,log warning。"""
    httpx_mock.add_response(url=ITENS_URL_PATTERN, json={"unexpected": "shape"})

    async with HttpClient(rate_limit_per_second=0) as client:
        result = await fetch_itens("01612441000107", 2026, 76, client=client)

    assert result == []
