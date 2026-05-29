"""tests/unit/test_utils.py — core/utils 的单元测试。"""
from __future__ import annotations

import pytest

from src.core.utils import parse_pncp_id


def test_parse_pncp_id_basic() -> None:
    """真实示例:01612441000107-1-000076/2026 → (cnpj, ano, seq)。"""
    cnpj, ano, seq = parse_pncp_id("01612441000107-1-000076/2026")
    assert cnpj == "01612441000107"
    assert ano == 2026
    assert seq == 76  # 前导零被剥掉


def test_parse_pncp_id_large_seq() -> None:
    """seq 不带前导零的情况。"""
    cnpj, ano, seq = parse_pncp_id("88594999000195-1-000801/2026")
    assert seq == 801


def test_parse_pncp_id_huge_seq() -> None:
    """6+ 位 seq。"""
    cnpj, ano, seq = parse_pncp_id("07954480000179-1-026456/2025")
    assert seq == 26456
    assert ano == 2025


def test_parse_pncp_id_tipo_other_than_1() -> None:
    """tipo=3 也能解析(合同 endpoint 用)。"""
    cnpj, ano, seq = parse_pncp_id("00394544000185-3-001007/2026")
    assert cnpj == "00394544000185"
    assert ano == 2026
    assert seq == 1007


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "not-a-pncp-id",
        "01612441000107-000076/2026",  # 缺 tipo
        "01612441000107-1-000076-2026",  # 用 - 而不是 /
        "0161244-1-000076/2026",  # cnpj 长度错
        "01612441000107-1-000076/202",  # ano 长度错
    ],
)
def test_parse_pncp_id_rejects_invalid(bad_id: str) -> None:
    with pytest.raises(ValueError, match="invalid pncp_id format"):
        parse_pncp_id(bad_id)
