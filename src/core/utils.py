"""跨层共用的小工具(无业务逻辑,无外部依赖)。"""
from __future__ import annotations

import re

# PNCP 控制号格式: {cnpj:14}-{tipo:1}-{seq:N}/{ano:4}
#   tipo 1 = 采购(Contratação),3 = 合同(Contrato),...
#   seq 可能带前导零("000076")也可能不带,统一转 int
#   ano 4 位
_PNCP_ID_RE = re.compile(r"^(?P<cnpj>\d{14})-(?P<tipo>\d)-(?P<seq>\d+)/(?P<ano>\d{4})$")


def parse_pncp_id(pncp_id: str) -> tuple[str, int, int]:
    """解析 PNCP 控制号为 ``(cnpj, ano, seq)``。

    PNCP 的 ``numeroControlePNCP`` 是 ``{cnpj}-{tipo}-{seq}/{ano}`` 格式,
    例如 ``01612441000107-1-000076/2026``。
    详情接口拿明细要用 ``cnpj``, ``ano``, ``seq`` 三个 path 参数:
    ``/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens``。

    Args:
        pncp_id: 形如 ``01612441000107-1-000076/2026``。

    Returns:
        ``(cnpj, ano, seq)`` — ``cnpj`` 14 位字符串,``ano`` / ``seq`` 已转 int
        (seq 不带前导零)。

    Raises:
        ValueError: 格式不匹配。

    Example::

        >>> parse_pncp_id("01612441000107-1-000076/2026")
        ('01612441000107', 2026, 76)
    """
    m = _PNCP_ID_RE.match(pncp_id)
    if not m:
        raise ValueError(f"invalid pncp_id format: {pncp_id!r}")
    return m["cnpj"], int(m["ano"]), int(m["seq"])


__all__ = ["parse_pncp_id"]
