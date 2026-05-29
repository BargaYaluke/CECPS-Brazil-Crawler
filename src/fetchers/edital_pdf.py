"""PNCP Edital PDF 附件 fetcher + 文本解析。

两步:
    1. :func:`fetch_arquivos` — 拉 ``/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos``,
       返回 PDF 元数据列表(标题 / url / 类型 / 序号)。
    2. :func:`download_and_extract_text` — **流式下载** PDF 到内存
       (``io.BytesIO``,不落盘),用 ``pdfplumber`` 提取全文,**解析完丢弃 PDF**。

策略(方案 C):
    * 只下"主 Edital"(``sequencialDocumento`` 最小的 Edital 类型文档)
    * 流式下载到内存,提取文本后字节即丢
    * 超大 PDF(> ``MAX_PDF_BYTES``)跳过,防 OOM
    * 扫描件(提取不到文本)→ 返回空串,不报错
    * ``pdfplumber`` 是同步 CPU 密集(一个 39 页 PDF ~15s),用
      ``asyncio.to_thread`` 丢线程池,不阻塞 event loop

反爬:此接口在 ``/api/pncp/v1/*`` 路径,**不被 F5 拦**,用普通 HttpClient。
PDF 下载链接(``pncp-api``)也开放。
"""
from __future__ import annotations

import asyncio
import io
from typing import Any

import httpx

from ..core.exceptions import HttpError
from ..core.http_client import HttpClient
from ..core.logger import logger
from ..core.settings import load_settings

_DEFAULT_V1_BASE = "https://pncp.gov.br/api/pncp/v1"

# 超过这个大小的 PDF 跳过(防超大文件 OOM / 拖慢)。默认 30 MB。
MAX_PDF_BYTES = 30 * 1024 * 1024


def _v1_base_url() -> str:
    try:
        cfg = load_settings().get("pncp", {}) or {}
        return str(cfg.get("v1_base_url", _DEFAULT_V1_BASE)).rstrip("/")
    except Exception:
        return _DEFAULT_V1_BASE


def _arquivos_url(cnpj: str, ano: int, seq: int) -> str:
    return f"{_v1_base_url()}/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos"


async def fetch_arquivos(
    cnpj: str,
    ano: int,
    seq: int,
    *,
    client: HttpClient | None = None,
) -> list[dict[str, Any]]:
    """拉取某招标的 PDF 附件元数据列表。

    Args:
        cnpj / ano / seq: 招标定位三元组(来自 ``parse_pncp_id``)。
        client: 已就绪的 HttpClient;``None`` 时自建临时实例。

    Returns:
        附件 dict 列表(原样);空 / 非 list → ``[]``。

    Raises:
        HttpError: 4xx(404 等)/ 5xx / 网络错。
    """
    url = _arquivos_url(cnpj, ano, seq)
    log = logger.bind(endpoint="arquivos", cnpj=cnpj, ano=ano, seq=seq)
    log.info("fetcher.arquivos.start")

    if client is not None:
        data = await client.get_json(url)
    else:
        async with HttpClient() as c:
            data = await c.get_json(url)

    if not isinstance(data, list):
        log.warning("fetcher.arquivos.no_content")
        return []
    log.bind(n_arquivos=len(data)).info("fetcher.arquivos.done")
    return data


def select_main_edital(arquivos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从附件列表里挑"主 Edital"。

    规则:类型为 Edital(``tipoDocumentoNome == "Edital"``)且
    ``sequencialDocumento`` 最小的那个;若没有 Edital 类型,退而取
    ``sequencialDocumento`` 最小的任意文档;空列表返回 ``None``。
    """
    if not arquivos:
        return None
    editais = [a for a in arquivos if (a.get("tipoDocumentoNome") or "") == "Edital"]
    pool = editais or arquivos
    return min(pool, key=lambda a: a.get("sequencialDocumento") or 1_000_000)


def _extract_text_sync(pdf_bytes: bytes) -> str:
    """同步:用 pdfplumber 从 PDF 字节提取全文。供 ``to_thread`` 调用。"""
    import pdfplumber

    texts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    return "\n".join(texts)


async def download_and_extract_text(
    pdf_url: str,
    *,
    client: HttpClient | None = None,
    max_bytes: int = MAX_PDF_BYTES,
) -> str:
    """流式下载 PDF 到内存 → pdfplumber 提取文本 → 丢弃 PDF。

    Args:
        pdf_url: PDF 下载链接(arquivos 元数据里的 ``url``)。
        client: 复用的 HttpClient(用它底层的 httpx.AsyncClient 做 stream);
            ``None`` 时自建临时 httpx 客户端。
        max_bytes: 超过则跳过下载,返回 ``""``。

    Returns:
        提取的全文文本;扫描件 / 超大 / 解析失败 → ``""``(不抛错,
        让 orchestrator 标记 extracted=true 但 text 空,不重复尝试)。
    """
    log = logger.bind(pdf_url=pdf_url[:80])

    # 拿一个 httpx.AsyncClient 来 stream(HttpClient 内部持有,但没暴露 stream;
    # 这里直接新建一个临时的,带浏览器 UA + 大超时)
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/pdf,*/*"}
    own_client = client is None or client._client is None  # type: ignore[attr-defined]
    httpx_client = (
        httpx.AsyncClient(timeout=120, follow_redirects=True, headers=headers)
        if own_client
        else client._client  # type: ignore[attr-defined]
    )

    try:
        pdf_bytes = bytearray()
        async with httpx_client.stream("GET", pdf_url, headers=headers) as resp:
            if resp.status_code >= 400:
                log.bind(status=resp.status_code).warning("fetcher.pdf.download_http_error")
                return ""
            async for chunk in resp.aiter_bytes():
                pdf_bytes.extend(chunk)
                if len(pdf_bytes) > max_bytes:
                    log.bind(size=len(pdf_bytes), max=max_bytes).warning(
                        "fetcher.pdf.too_large_skip"
                    )
                    return ""
    except (httpx.TransportError, httpx.HTTPError) as exc:
        log.bind(error=str(exc)).warning("fetcher.pdf.download_failed")
        return ""
    finally:
        if own_client:
            await httpx_client.aclose()

    if not pdf_bytes:
        return ""

    # pdfplumber 是同步 CPU 密集(大 PDF 十几秒),丢线程池
    try:
        text = await asyncio.to_thread(_extract_text_sync, bytes(pdf_bytes))
    except Exception as exc:
        log.bind(error=str(exc), size=len(pdf_bytes)).warning("fetcher.pdf.parse_failed")
        return ""

    log.bind(pdf_bytes=len(pdf_bytes), text_len=len(text)).info("fetcher.pdf.extracted")
    return text


async def fetch_edital_text(
    cnpj: str,
    ano: int,
    seq: int,
    *,
    client: HttpClient | None = None,
) -> tuple[str | None, str]:
    """便捷封装:拉 arquivos → 挑主 Edital → 下载解析。

    Returns:
        ``(edital_pdf_url, edital_text)``;没有 Edital 附件时返回 ``(None, "")``。
    """
    arquivos = await fetch_arquivos(cnpj, ano, seq, client=client)
    main = select_main_edital(arquivos)
    if main is None:
        return None, ""
    url = main.get("url") or main.get("uri") or ""
    if not url:
        return None, ""
    text = await download_and_extract_text(url, client=client)
    return url, text


__all__ = [
    "fetch_arquivos",
    "select_main_edital",
    "download_and_extract_text",
    "fetch_edital_text",
    "MAX_PDF_BYTES",
]
