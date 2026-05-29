"""tests/unit/test_excel_exporter.py — Excel 导出单测。

用内存 DB(monkeypatch session 用的 engine)造少量数据,导出后用 openpyxl
读回验证 sheet 名、表头、维度中文化、金额格式。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.storage import (
    Base,
    Contratacao,
    Contrato,
    Item,
    Orgao,
    PcaItem,
)


@pytest.fixture
def seeded_engine(monkeypatch: pytest.MonkeyPatch):
    """内存 DB + 少量数据;monkeypatch 让 exporter 的 session_scope 用它。"""
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)

    with Session(eng) as s:
        s.add(
            Contratacao(
                pncp_id="x-1-001/2026",
                modalidade_nome="Pregão - Eletrônico",
                dimension_primary="healthcare",
                dimension_secondary=["digital_economy"],
                dimension_confidence=0.75,
                objeto_compra="Aquisição de medicamentos",
                valor_total_estimado=123456.78,
                valor_cny_estimado=165432.08,
                region_macro="Centro-Oeste",
                region_gdp_tier="middle",
                orgao_razao_social="MINISTÉRIO DA SAÚDE",
                orgao_cnpj="00394544000185",
                uf_sigla="DF",
                data_publicacao_pncp=datetime(2026, 5, 26, 10, 0, 0),
                is_e_auction=True,
            )
        )
        s.add(
            Contratacao(
                pncp_id="y-1-002/2026",
                modalidade_nome="Concorrência - Eletrônica",
                dimension_primary="commodities",
                # 内嵌非法控制字符(换页符 \x0c / 垂直制表符 \x0b)——PNCP 真实数据里出现过
                objeto_compra="Óleo\x0c diesel\x0b LACEN",
                valor_total_estimado=999.0,
                uf_sigla="SP",
                is_competitive_bid=True,
            )
        )
        s.add(Item(pncp_id="x-1-001/2026", numero_item=1, descricao="Dipirona", valor_total=50.0))
        s.add(
            Contrato(
                pncp_id="x-2-001/2026",
                linked_contratacao_pncp_id="x-1-001/2026",
                nome_razao_social_fornecedor="FARMA LTDA",
                valor_global=100000.0,
                data_assinatura=date(2026, 5, 20),
            )
        )
        s.add(
            PcaItem(
                id_pca_pncp="z-0-001/2027",
                numero_item=1,
                ano_pca=2027,
                descricao_item="Equipamento hospitalar",
                valor_total=500000.0,
                data_desejada=date(2027, 3, 1),
            )
        )
        s.add(
            Orgao(
                cnpj="00394544000185",
                nome="MINISTÉRIO DA SAÚDE",
                esfera="F",
                uf="DF",
                municipio="Brasília",
                total_contratacoes_2y=5,
                total_valor_2y=60_000_000.0,
                tier="high",
                last_active_date=date(2026, 5, 26),
            )
        )
        s.commit()

    # exporter 内部用 session_scope → get_engine();patch 成内存 eng
    monkeypatch.setattr("src.storage.database.get_engine", lambda *a, **kw: eng)
    # filtered_log 目录指到不存在的临时位置(导出空审计 sheet)
    monkeypatch.setattr(
        "src.exporters.excel.get_filter_settings",
        lambda *a, **kw: {"mode": "hard_delete", "filtered_log_dir": "/nonexistent"},
    )
    yield eng
    eng.dispose()


def test_export_creates_all_sheets(seeded_engine, tmp_path: Path) -> None:
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    stats = export_to_excel(out)

    assert out.exists()
    wb = load_workbook(out)
    assert set(wb.sheetnames) == {
        "招标主表",
        "采购明细",
        "已签合同",
        "年度采购计划",
        "机构画像",
        "维度透视",
        "过滤审计",
    }
    assert stats["招标主表"] == 2
    assert stats["采购明细"] == 1
    assert stats["已签合同"] == 1
    assert stats["年度采购计划"] == 1
    assert stats["机构画像"] == 1


def test_illegal_control_chars_are_stripped(seeded_engine, tmp_path: Path) -> None:
    """含控制字符(\\x0c/\\x0b)的 PNCP 文本不会让导出崩,且字符被清掉。"""
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    export_to_excel(out)  # 不抛 IllegalCharacterError 即过第一关
    wb = load_workbook(out)
    ws = wb["招标主表"]
    headers = [c.value for c in ws[1]]
    obj_col = headers.index("标的") + 1
    texts = [ws.cell(row=r, column=obj_col).value for r in range(2, ws.max_row + 1)]
    target = next((t for t in texts if t and "LACEN" in t), None)
    assert target is not None
    assert "\x0c" not in target and "\x0b" not in target  # 控制字符已剔除
    assert "Óleo diesel LACEN" == target  # 清洗后文本连续


def test_enrichment_columns_present_and_localized(seeded_engine, tmp_path: Path) -> None:
    """招标主表带富化列(CNY 金额 / 大区 / GDP分层);GDP 分层中文化。"""
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    export_to_excel(out)
    wb = load_workbook(out)
    ws = wb["招标主表"]
    headers = [c.value for c in ws[1]]

    # 富化列都在
    assert "预估金额(CNY)" in headers
    assert "大区" in headers
    assert "GDP分层" in headers

    # CNY 金额是 money 格式
    cny_col = headers.index("预估金额(CNY)") + 1
    assert ws.cell(row=2, column=cny_col).number_format == "#,##0.00"

    # GDP 分层 middle → 中
    gdp_col = headers.index("GDP分层") + 1
    gdp_values = {ws.cell(row=r, column=gdp_col).value for r in range(2, ws.max_row + 1)}
    assert "中" in gdp_values
    assert "middle" not in gdp_values  # 不该出现英文


def test_orgaos_sheet_has_profile(seeded_engine, tmp_path: Path) -> None:
    """机构画像 Sheet:有数据 + 分层中文化(high → 高)。"""
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    export_to_excel(out)
    wb = load_workbook(out)
    ws = wb["机构画像"]
    headers = [c.value for c in ws[1]]
    assert "近2年累计额(BRL)" in headers
    assert "机构分层" in headers

    tier_col = headers.index("机构分层") + 1
    tier_values = {ws.cell(row=r, column=tier_col).value for r in range(2, ws.max_row + 1)}
    assert "高" in tier_values


def test_dimension_is_localized_to_chinese(seeded_engine, tmp_path: Path) -> None:
    """主维度列应该是中文(healthcare → 医疗医药)。"""
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    export_to_excel(out)
    wb = load_workbook(out)
    ws = wb["招标主表"]

    # 找"主维度"列
    headers = [c.value for c in ws[1]]
    dim_col = headers.index("主维度") + 1
    values = {ws.cell(row=r, column=dim_col).value for r in range(2, ws.max_row + 1)}
    assert "医疗医药" in values
    assert "大宗商贸" in values
    # 不该出现英文 key
    assert "healthcare" not in values


def test_header_frozen_and_filtered(seeded_engine, tmp_path: Path) -> None:
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    export_to_excel(out)
    wb = load_workbook(out)
    ws = wb["招标主表"]
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref is not None


def test_money_format_applied(seeded_engine, tmp_path: Path) -> None:
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    export_to_excel(out)
    wb = load_workbook(out)
    ws = wb["招标主表"]
    headers = [c.value for c in ws[1]]
    money_col = headers.index("预估金额(BRL)") + 1
    cell = ws.cell(row=2, column=money_col)
    assert cell.number_format == "#,##0.00"


def test_pivot_aggregates(seeded_engine, tmp_path: Path) -> None:
    """维度透视:2 条招标分属 2 个维度,各 1 条。"""
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    export_to_excel(out)
    wb = load_workbook(out)
    ws = wb["维度透视"]
    # 表头 + 至少 2 行数据
    assert ws.max_row >= 3
    dims = {ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)}
    assert "医疗医药" in dims
    assert "大宗商贸" in dims


def test_bool_localized(seeded_engine, tmp_path: Path) -> None:
    """布尔列显示 是/否。"""
    from src.exporters.excel import export_to_excel

    out = tmp_path / "report.xlsx"
    export_to_excel(out)
    wb = load_workbook(out)
    ws = wb["招标主表"]
    headers = [c.value for c in ws[1]]
    col = headers.index("电子拍卖") + 1
    values = {ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)}
    assert "是" in values or "否" in values
