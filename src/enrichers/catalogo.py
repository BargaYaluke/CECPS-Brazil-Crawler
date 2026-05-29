"""目录富化:用 ``catalogo_compras`` 维表把 itens 的裸编码映射成可读类目。

``itens.catalogo_codigo_item`` 是 CATMAT/CATSER 的裸编码;查 ``catalogo_compras``
(由 ``fetch-catalogo`` 灌的 Compras.gov.br 标准品类树)→ 取最可读的类目名,
回填 ``itens.categoria_item_catalogo``(原本常为空)。

纯查表逻辑(给定一行目录数据 → 类目名);DB 遍历在
:func:`src.pipeline.orchestrator.run_enrichment`。
"""
from __future__ import annotations

from typing import Protocol


class _CatalogoRow(Protocol):
    """``catalogo_compras`` 行的鸭子类型(只用到这三列)。"""

    nome_pdm: str | None
    nome_classe: str | None
    nome_grupo: str | None


def derive_categoria(row: _CatalogoRow | None) -> str | None:
    """从一行目录数据选**最细可读**的类目名。

    优先级:PDM(最具体,如「CADEIRA ESCRITÓRIO」)> 类(classe)> 大类(grupo)。

    Args:
        row: ``catalogo_compras`` 行(或 None)。

    Returns:
        类目名字符串;全空或 row 为 None 时返回 ``None``。
    """
    if row is None:
        return None
    return row.nome_pdm or row.nome_classe or row.nome_grupo


__all__ = ["derive_categoria"]
