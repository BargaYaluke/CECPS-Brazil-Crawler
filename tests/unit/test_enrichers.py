"""tests/unit/test_enrichers.py — 富化层模块单测(region / fx)。

region 纯离线查表;fx 用 ``pytest_httpx`` mock。
"""
from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

from src.core.http_client import HttpClient
from src.enrichers import (
    fetch_brl_to_cny_rate,
    lookup_region,
    to_cny,
)


# ─── region(离线查 regions.yaml)───────────────────────────────────────


def test_lookup_region_known_uf() -> None:
    assert lookup_region("SP") == ("Sudeste", "high")
    assert lookup_region("am") == ("Norte", "low")  # 大小写不敏感
    assert lookup_region(" RS ") == ("Sul", "high")  # 去空白
    assert lookup_region("DF") == ("Centro-Oeste", "middle")


def test_lookup_region_unknown_or_empty() -> None:
    assert lookup_region("ZZ") == (None, None)
    assert lookup_region(None) == (None, None)
    assert lookup_region("") == (None, None)


# ─── fx(BRL→CNY,AwesomeAPI)─────────────────────────────────────────


FX_URL = re.compile(r"https://economia\.awesomeapi\.com\.br/json/last/BRL-CNY")


async def test_fetch_brl_to_cny_rate(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=FX_URL, json={"BRLCNY": {"bid": "1.3396", "create_date": "2026-05-28 03:35:23"}}
    )
    async with HttpClient(rate_limit_per_second=0) as client:
        rate = await fetch_brl_to_cny_rate(client=client)
    assert rate == pytest.approx(1.3396)


async def test_fetch_brl_to_cny_rate_bad_payload_returns_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=FX_URL, json={"unexpected": "shape"})
    async with HttpClient(rate_limit_per_second=0) as client:
        rate = await fetch_brl_to_cny_rate(client=client)
    assert rate is None


def test_to_cny() -> None:
    assert to_cny(1000.0, 1.34) == pytest.approx(1340.0)
    assert to_cny(None, 1.34) is None
    assert to_cny(1000.0, None) is None
