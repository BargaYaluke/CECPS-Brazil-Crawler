"""区域富化:UF(州)→ macro region + GDP 分层。

**纯离线查表**,读 ``config/regions.yaml``(IBGE 五大区 + 粗略 GDP 分层),
不调任何外部 API —— 因为 ``contratacoes.uf_sigla`` 是 PNCP 自带字段,区域/GDP
只是它的确定性衍生维度(契合 CLAUDE.md:规则即配置,改 yaml 不改代码)。

填充目标列:``contratacoes.region_macro`` / ``contratacoes.region_gdp_tier``。
DB 遍历更新在 :func:`src.pipeline.orchestrator.run_enrichment`。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from ..core.exceptions import ConfigError
from ..core.logger import logger
from ..core.settings import DEFAULT_CONFIG_DIR


@lru_cache(maxsize=4)
def _load_region_maps(config_dir: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """读 ``regions.yaml``,返回 ``(uf->macro_region, uf->gdp_tier)`` 两个映射。

    Raises:
        ConfigError: regions.yaml 不存在或解析失败。
    """
    base = config_dir or DEFAULT_CONFIG_DIR
    path = base / "regions.yaml"
    if not path.exists():
        raise ConfigError(f"regions.yaml not found at {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc

    macro_map: dict[str, str] = {}
    for region, ufs in (data.get("macro_regions") or {}).items():
        for uf in ufs or []:
            macro_map[str(uf).strip().upper()] = region

    gdp_map: dict[str, str] = {}
    for tier, ufs in (data.get("gdp_tiers") or {}).items():
        for uf in ufs or []:
            gdp_map[str(uf).strip().upper()] = tier

    logger.bind(n_uf_macro=len(macro_map), n_uf_gdp=len(gdp_map)).debug(
        "enricher.region.maps_loaded"
    )
    return macro_map, gdp_map


def lookup_region(
    uf: str | None, config_dir: Path | None = None
) -> tuple[str | None, str | None]:
    """UF → ``(region_macro, region_gdp_tier)``;未知 UF 或空值返回 ``(None, None)``。

    Args:
        uf: 州两字母代码(如 ``SP``);大小写 / 空白不敏感。
        config_dir: 配置目录覆盖(测试用)。

    Returns:
        ``(macro_region, gdp_tier)`` 二元组。
    """
    if not uf:
        return None, None
    key = uf.strip().upper()
    macro_map, gdp_map = _load_region_maps(config_dir)
    return macro_map.get(key), gdp_map.get(key)


__all__ = ["lookup_region"]
