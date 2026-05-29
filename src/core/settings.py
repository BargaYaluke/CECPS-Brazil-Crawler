"""轻量配置加载器。

读取 ``config/settings.yaml`` 为 dict,供 http_client 等核心模块使用。
更完整的 pydantic-settings 建模留待 P1 阶段(届时迁移到这里的同一接口)。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigError

DEFAULT_CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "./config")).resolve()


@lru_cache(maxsize=8)
def load_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """加载 ``settings.yaml`` 为字典。

    Args:
        config_dir: 配置目录;``None`` 时使用 ``CONFIG_DIR`` 环境变量或 ``./config``。

    Returns:
        解析后的字典。

    Raises:
        ConfigError: 文件不存在或 YAML 解析失败。
    """
    base = config_dir or DEFAULT_CONFIG_DIR
    settings_path = base / "settings.yaml"
    if not settings_path.exists():
        raise ConfigError(f"settings.yaml not found at {settings_path}")
    try:
        with settings_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse {settings_path}: {exc}") from exc
    return data


# ─── 子节点访问器(带默认值兜底) ───────────────────────────────────────

_HTTP_DEFAULTS: dict[str, Any] = {
    "base_url": "https://pncp.gov.br",
    "timeout": 30,
    "rate_limit_per_second": 10,
    "max_retries": 3,
    "user_agent": "BrazilProcurementBot/1.0",
}

_PNCP_DEFAULTS: dict[str, Any] = {
    "base_url": "https://pncp.gov.br/api/consulta/v1",
    "timeout": 30,
    "rate_limit_per_sec": 10,
    "page_size": 50,
    # 列表接口传输方式:auto(httpx 优先,撞 F5 自动切浏览器)/ httpx / browser
    "transport": "auto",
}

_BROWSER_DEFAULTS: dict[str, Any] = {
    "headless": True,
    "wait_after_load_ms": 5000,
    "navigation_timeout_ms": 60000,
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "challenge_url": "https://pncp.gov.br/app/editais",
    "cookie_name_prefixes": ["TS"],
}


def get_http_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """返回 ``http`` 节点配置,缺失字段用默认值兜底。"""
    try:
        raw = load_settings(config_dir).get("http", {}) or {}
    except ConfigError:
        # 找不到 settings.yaml 时,允许走纯默认值(便于单元测试)。
        raw = {}
    return {**_HTTP_DEFAULTS, **raw}


def get_pncp_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """返回 ``pncp`` 节点配置(供 fetchers 使用),缺失字段走默认值兜底。"""
    try:
        raw = load_settings(config_dir).get("pncp", {}) or {}
    except ConfigError:
        raw = {}
    return {**_PNCP_DEFAULTS, **raw}


def get_browser_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """返回 ``browser`` 节点配置(供 core/browser_client 用),缺失字段走默认值兜底。"""
    try:
        raw = load_settings(config_dir).get("browser", {}) or {}
    except ConfigError:
        raw = {}
    return {**_BROWSER_DEFAULTS, **raw}


_COMPRAS_DEFAULTS: dict[str, Any] = {
    "base_url": "https://dadosabertos.compras.gov.br",
    "timeout": 60,
    "rate_limit_per_sec": 8,
    "page_size": 500,
}


def get_compras_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """返回 ``compras_gov`` 节点配置(供 Compras.gov.br fetchers 使用),缺失字段兜底。"""
    try:
        raw = load_settings(config_dir).get("compras_gov", {}) or {}
    except ConfigError:
        raw = {}
    return {**_COMPRAS_DEFAULTS, **raw}


_ENRICHMENT_DEFAULTS: dict[str, Any] = {
    "fx_brl_cny_url": "https://economia.awesomeapi.com.br/json/last/BRL-CNY",
    "brasilapi_base_url": "https://brasilapi.com.br/api",
    "rate_limit_per_sec": 3,
}


def get_enrichment_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """返回 ``enrichment`` 节点配置(供 enrichers 使用),缺失字段兜底。"""
    try:
        raw = load_settings(config_dir).get("enrichment", {}) or {}
    except ConfigError:
        raw = {}
    return {**_ENRICHMENT_DEFAULTS, **raw}


_FILTER_DEFAULTS: dict[str, Any] = {
    "mode": "hard_delete",
    "filtered_log_dir": "./data/filtered_log",
}


def get_filter_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """返回 ``filter`` 节点配置(模式 / filtered_log 目录),缺失字段兜底。"""
    try:
        raw = load_settings(config_dir).get("filter", {}) or {}
    except ConfigError:
        raw = {}
    return {**_FILTER_DEFAULTS, **raw}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc


def get_default_modalidade_iter(config_dir: Path | None = None) -> list[int]:
    """返回 ``config/modalidades.yaml`` 的 ``default_iter`` 列表。"""
    base = config_dir or DEFAULT_CONFIG_DIR
    data = _load_yaml(base / "modalidades.yaml")
    items = data.get("default_iter") or []
    return [int(x) for x in items]


def get_modalidades_dict(config_dir: Path | None = None) -> dict[int, dict[str, str]]:
    """返回 ``{codigo: {nome_pt, nome_zh}}`` 字典,用于日志富化。"""
    base = config_dir or DEFAULT_CONFIG_DIR
    data = _load_yaml(base / "modalidades.yaml")
    out: dict[int, dict[str, str]] = {}
    for item in data.get("modalidades") or []:
        codigo = item.get("codigo")
        if codigo is None:
            continue
        out[int(codigo)] = {
            "nome_pt": item.get("nome_pt", ""),
            "nome_zh": item.get("nome_zh", ""),
        }
    return out


__all__ = [
    "DEFAULT_CONFIG_DIR",
    "load_settings",
    "get_http_settings",
    "get_pncp_settings",
    "get_browser_settings",
    "get_compras_settings",
    "get_enrichment_settings",
    "get_filter_settings",
    "get_default_modalidade_iter",
    "get_modalidades_dict",
]
