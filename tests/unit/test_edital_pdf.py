"""tests/unit/test_edital_pdf.py — Edital PDF fetcher 单测。

不打真实网络、不依赖真实 PDF 文件:
- arquivos 元数据用 pytest_httpx mock
- PDF 下载用 pytest_httpx mock 字节流
- pdfplumber 解析用 monkeypatch(真实解析能力已在探测时人工验证:39 页 → 11 万字符)
"""
from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

from src.core.http_client import HttpClient
from src.fetchers import edital_pdf
from src.fetchers.edital_pdf import (
    download_and_extract_text,
    fetch_arquivos,
    select_main_edital,
)

ARQUIVOS_URL_RE = re.compile(
    r"https://pncp\.gov\.br/api/pncp/v1/orgaos/\d+/compras/\d+/\d+/arquivos"
)


# ─── select_main_edital(纯函数,无网络)───────────────────────────────


def test_select_main_edital_picks_lowest_sequencial() -> None:
    """多个 Edital → 取 sequencialDocumento 最小的。"""
    arquivos = [
        {"tipoDocumentoNome": "Edital", "sequencialDocumento": 2, "titulo": "B"},
        {"tipoDocumentoNome": "Edital", "sequencialDocumento": 1, "titulo": "A"},
    ]
    main = select_main_edital(arquivos)
    assert main is not None
    assert main["titulo"] == "A"


def test_select_main_edital_prefers_edital_type() -> None:
    """有 Edital 类型时优先,即使别的类型 sequencial 更小。"""
    arquivos = [
        {"tipoDocumentoNome": "Anexo", "sequencialDocumento": 1, "titulo": "anexo"},
        {"tipoDocumentoNome": "Edital", "sequencialDocumento": 2, "titulo": "edital"},
    ]
    main = select_main_edital(arquivos)
    assert main["titulo"] == "edital"


def test_select_main_edital_fallback_when_no_edital() -> None:
    """没有 Edital 类型 → 退而取 sequencial 最小的任意文档。"""
    arquivos = [
        {"tipoDocumentoNome": "Anexo", "sequencialDocumento": 3, "titulo": "x"},
        {"tipoDocumentoNome": "Aviso", "sequencialDocumento": 1, "titulo": "y"},
    ]
    main = select_main_edital(arquivos)
    assert main["titulo"] == "y"


def test_select_main_edital_empty() -> None:
    assert select_main_edital([]) is None


# ─── fetch_arquivos ────────────────────────────────────────────────────


async def test_fetch_arquivos_returns_list(httpx_mock: HTTPXMock) -> None:
    sample = [
        {"tipoDocumentoNome": "Edital", "sequencialDocumento": 1, "url": "u1"},
        {"tipoDocumentoNome": "Edital", "sequencialDocumento": 2, "url": "u2"},
    ]
    httpx_mock.add_response(url=ARQUIVOS_URL_RE, json=sample)

    async with HttpClient(rate_limit_per_second=0) as client:
        result = await fetch_arquivos("01612441000107", 2026, 76, client=client)

    assert len(result) == 2
    req = httpx_mock.get_requests()[0]
    assert "/orgaos/01612441000107/compras/2026/76/arquivos" in str(req.url)


async def test_fetch_arquivos_non_list_returns_empty(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ARQUIVOS_URL_RE, json={"unexpected": "shape"})
    async with HttpClient(rate_limit_per_second=0) as client:
        result = await fetch_arquivos("01612441000107", 2026, 76, client=client)
    assert result == []


# ─── download_and_extract_text ─────────────────────────────────────────


async def test_download_and_extract_text(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """下载字节 + 解析(解析 mock 成返回固定文本)。"""
    fake_pdf = b"%PDF-1.4 fake bytes"
    httpx_mock.add_response(
        url="https://pncp.gov.br/pncp-api/v1/.../arquivos/1",
        content=fake_pdf,
    )

    # mock pdfplumber 解析(避免真解析假 PDF 崩)
    monkeypatch.setattr(
        edital_pdf,
        "_extract_text_sync",
        lambda b: "TEXTO DO EDITAL com PPB obrigatório",
    )

    text = await download_and_extract_text(
        "https://pncp.gov.br/pncp-api/v1/.../arquivos/1"
    )
    assert "PPB" in text


async def test_download_too_large_returns_empty(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过 max_bytes → 返回空,不解析。"""
    big = b"x" * 5000
    httpx_mock.add_response(
        url="https://pncp.gov.br/pncp-api/v1/.../arquivos/1", content=big
    )
    # 故意把 _extract_text_sync 设成会失败的,确保它没被调用
    monkeypatch.setattr(
        edital_pdf,
        "_extract_text_sync",
        lambda b: (_ for _ in ()).throw(AssertionError("不该被调用")),
    )

    text = await download_and_extract_text(
        "https://pncp.gov.br/pncp-api/v1/.../arquivos/1", max_bytes=1000
    )
    assert text == ""


async def test_download_http_error_returns_empty(httpx_mock: HTTPXMock) -> None:
    """PDF 服务器 500 → 返回空,不抛错。"""
    httpx_mock.add_response(
        url="https://pncp.gov.br/pncp-api/v1/.../arquivos/1", status_code=500
    )
    text = await download_and_extract_text(
        "https://pncp.gov.br/pncp-api/v1/.../arquivos/1"
    )
    assert text == ""


async def test_extract_text_sync_parse_failure_returns_empty(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pdfplumber 解析抛异常(损坏 PDF)→ 返回空,不崩。"""
    httpx_mock.add_response(
        url="https://pncp.gov.br/pncp-api/v1/.../arquivos/1", content=b"broken"
    )

    def _boom(b: bytes) -> str:
        raise ValueError("corrupt pdf")

    monkeypatch.setattr(edital_pdf, "_extract_text_sync", _boom)
    text = await download_and_extract_text(
        "https://pncp.gov.br/pncp-api/v1/.../arquivos/1"
    )
    assert text == ""
