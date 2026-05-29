"""tests/unit/test_enrichers.py — 富化层模块单测(region / fx / cnpj / catalogo)。

region 纯离线查表;fx / cnpj 用 ``pytest_httpx`` mock;catalogo 是纯查表逻辑。
"""
from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

from src.core.http_client import HttpClient
from src.enrichers import (
    derive_categoria,
    fetch_brl_to_cny_rate,
    fetch_cnpj_profile,
    lookup_region,
    normalize_cnpj,
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


# ─── cnpj(BrasilAPI)──────────────────────────────────────────────────


def test_normalize_cnpj() -> None:
    assert normalize_cnpj("00.000.000/0001-91") == "00000000000191"
    assert normalize_cnpj("00000000000191") == "00000000000191"
    assert normalize_cnpj("123") is None
    assert normalize_cnpj(None) is None


CNPJ_URL = re.compile(r"https://brasilapi\.com\.br/api/cnpj/v1/\d{14}")


async def test_fetch_cnpj_profile(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=CNPJ_URL,
        json={
            "razao_social": "BANCO X",
            "porte": "DEMAIS",
            "uf": "DF",
            "municipio": "BRASILIA",
            "cnae_fiscal_descricao": "Bancos",
        },
    )
    async with HttpClient(rate_limit_per_second=0) as client:
        prof = await fetch_cnpj_profile("00.000.000/0001-91", client=client)
    assert prof is not None
    assert prof["razao_social"] == "BANCO X"
    assert prof["cnpj"] == "00000000000191"  # 清洗后 14 位回填
    assert prof["uf"] == "DF"


async def test_fetch_cnpj_profile_404_returns_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=CNPJ_URL, status_code=404)
    async with HttpClient(rate_limit_per_second=0) as client:
        prof = await fetch_cnpj_profile("11111111000111", client=client)
    assert prof is None


async def test_fetch_cnpj_profile_invalid_cnpj_makes_no_request(httpx_mock: HTTPXMock) -> None:
    async with HttpClient(rate_limit_per_second=0) as client:
        prof = await fetch_cnpj_profile("123", client=client)
    assert prof is None
    assert len(httpx_mock.get_requests()) == 0


# ─── catalogo(纯查表:选最细可读类目)──────────────────────────────


class _Row:
    def __init__(self, pdm=None, classe=None, grupo=None) -> None:
        self.nome_pdm = pdm
        self.nome_classe = classe
        self.nome_grupo = grupo


def test_derive_categoria_prefers_most_specific() -> None:
    assert derive_categoria(_Row(pdm="CADEIRA", classe="MOBILIÁRIO", grupo="MOB")) == "CADEIRA"
    assert derive_categoria(_Row(classe="MOBILIÁRIO", grupo="MOB")) == "MOBILIÁRIO"
    assert derive_categoria(_Row(grupo="MOB")) == "MOB"
    assert derive_categoria(_Row()) is None
    assert derive_categoria(None) is None
