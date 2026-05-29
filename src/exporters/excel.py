"""Excel 多 Sheet 报告导出(openpyxl)。

把 SQLite 里的招标 / 明细 / 合同 / 年度计划 + 维度透视 + 过滤审计
导成一份业务方能直接打开分析的 ``.xlsx``。

Sheet 结构(docs/02 §4.4;P6 富化后扩到 7 个 Sheet):
    1. 招标主表       contratacoes(核心列 + 六大维度中文标签 + 富化:大区/GDP分层/CNY金额)
    2. 采购明细       itens(含目录富化出的「品类」列)
    3. 已签合同       contratos(竞品分析)
    4. 年度采购计划   pca_itens(商机前置)
    5. 机构画像       orgaos(P6 富化:近 2 年招标数 / 累计额 / 分层)
    6. 维度透视       按六大维度统计招标数 + 金额
    7. 过滤审计       filtered_log Parquet(被硬过滤的记录)

设计:
    * 中文表头(业务友好)
    * 维度英文 key → 中文(从 dimension_keywords.yaml 的 nome_zh 读)
    * GDP / 机构分层 high/middle/low → 高/中/低
    * 金额 / 日期 number_format
    * 冻结首行 + 自动筛选 + 列宽
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select

from ..classifiers.dimension_engine import get_default_dimension_engine
from ..core.logger import logger
from ..core.settings import get_filter_settings
from ..storage import Contratacao, Contrato, Item, Orgao, PcaItem, session_scope

# ─── 样式常量 ──────────────────────────────────────────────────────────

_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
_MONEY_FMT = "#,##0.00"
_DATE_FMT = "yyyy-mm-dd"
_DATETIME_FMT = "yyyy-mm-dd hh:mm"


# 列定义:(ORM 属性名 或 特殊 key, 中文表头, 格式类型, 列宽)
# 格式类型: text / money / date / datetime / bool / int / list
ColumnDef = tuple[str, str, str, int]

# GDP 分层 / 机构分层 high/middle/low → 中文
_TIER_ZH = {"high": "高", "middle": "中", "low": "低"}


def _dimension_zh_map() -> dict[str, str]:
    """``digital_economy`` → ``数字经济`` 映射(从词典 yaml 的 nome_zh)。"""
    rules = get_default_dimension_engine().rules
    return {dim: (ddef.nome_zh or dim) for dim, ddef in rules.dimensions.items()}


def _clean_str(s: str) -> str:
    """剔除 Excel/openpyxl 不接受的控制字符。

    PNCP 文本字段(objetoCompra 等)偶尔混入换页符 / 垂直制表符 / 空字节等不可见
    控制字符,openpyxl 写单元格时会抛 ``IllegalCharacterError``。导出前统一清洗。
    """
    return ILLEGAL_CHARACTERS_RE.sub("", s)


def _fmt_value(value: Any, kind: str) -> Any:
    """把 ORM 值转成 Excel 友好的写入值。"""
    if value is None:
        return None
    if kind == "bool":
        return "是" if value else "否"
    if kind in ("date", "datetime"):
        # openpyxl 接受 datetime/date 对象,配合 number_format
        return value
    if kind == "list":
        # dimension_secondary 是 JSON list
        if isinstance(value, list):
            return _clean_str(", ".join(str(v) for v in value))
        return _clean_str(str(value))
    if isinstance(value, str):
        return _clean_str(value)
    return value


_NUMFMT_BY_KIND = {"money": _MONEY_FMT, "date": _DATE_FMT, "datetime": _DATETIME_FMT}


def _add_sheet(
    wb: Workbook,
    title: str,
    columns: list[ColumnDef],
    rows: Iterable[dict[str, Any]],
    *,
    value_transform: Callable[[str, Any], Any] | None = None,
) -> int:
    """通用:用 **write_only 流式** 建一个带样式的 Sheet,返回写入的数据行数。

    内存恒定:``rows`` 是可迭代(通常是流式生成器),逐行 ``append`` 到 write_only
    worksheet(直接落临时文件,不在内存里堆 Cell 对象)。配合只查必要列的查询,
    可导出百万行级数据而不撑爆内存(见 :func:`_stream_columns`)。

    Args:
        wb: write_only 模式的 Workbook。
        title: Sheet 名。
        columns: 列定义列表。
        rows: 每行一个 dict(key 对应 columns 的属性名);可为生成器(惰性消费)。
        value_transform: 可选 ``(key, raw_value) -> display_value`` 钩子(维度中文化等)。

    Returns:
        写入的数据行数(不含表头)。
    """
    ws = wb.create_sheet(title=title)

    # 列宽 + 冻结首行(write_only 下需在 append 前设置)
    for col_idx, (_key, _header, _kind, width) in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.freeze_panes = "A2"

    # 表头(带样式)
    header_cells = []
    for (_key, header, _kind, _width) in columns:
        c = WriteOnlyCell(ws, value=header)
        c.fill = _HEADER_FILL
        c.font = _HEADER_FONT
        c.alignment = _HEADER_ALIGN
        header_cells.append(c)
    ws.append(header_cells)

    # 数据行(流式)
    n = 0
    for row in rows:
        out: list[Any] = []
        for (key, _header, kind, _width) in columns:
            raw = row.get(key)
            if value_transform is not None:
                raw = value_transform(key, raw)
            val = _fmt_value(raw, kind)
            numfmt = _NUMFMT_BY_KIND.get(kind)
            if numfmt is not None:
                c = WriteOnlyCell(ws, value=val)
                c.number_format = numfmt
                out.append(c)
            else:
                out.append(val)
        ws.append(out)
        n += 1

    if n:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{n + 1}"
    return n


def _stream_columns(
    session: Any,
    model: Any,
    columns: list[ColumnDef],
    *,
    order_by: Any | None = None,
    limit: int | None = None,
    yield_size: int = 2000,
) -> Iterator[dict[str, Any]]:
    """**只查 columns 里需要的列**(避开 raw_json 大字段)+ ``yield_per`` 流式产出行 dict。

    这是导出不撑爆内存的关键:``select(Model)`` 会加载完整 ORM 对象(含每行几 KB 的
    ``raw_json`` 原始留底),百万行级直接 OOM;这里只 SELECT 需要的列,逐批从游标取。
    """
    keys = [key for (key, _h, _k, _w) in columns]
    stmt = select(*[getattr(model, k) for k in keys])
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    if limit:
        stmt = stmt.limit(limit)
    for row in session.execute(stmt).yield_per(yield_size):
        yield dict(zip(keys, row))


# ─── 各 Sheet 列定义 ───────────────────────────────────────────────────

_CONTRATACOES_COLS: list[ColumnDef] = [
    ("pncp_id", "PNCP编号", "text", 30),
    ("modalidade_nome", "采购方式", "text", 18),
    ("dimension_primary", "主维度", "text", 12),
    ("dimension_secondary", "次维度", "list", 16),
    ("dimension_confidence", "维度置信度", "text", 10),
    ("objeto_compra", "标的", "text", 60),
    ("valor_total_estimado", "预估金额(BRL)", "money", 16),
    ("valor_cny_estimado", "预估金额(CNY)", "money", 16),
    ("orgao_razao_social", "采购机构", "text", 32),
    ("orgao_esfera_id", "级别", "text", 6),
    ("uf_sigla", "州", "text", 6),
    ("municipio_nome", "城市", "text", 18),
    ("region_macro", "大区", "text", 12),
    ("region_gdp_tier", "GDP分层", "text", 8),
    ("data_publicacao_pncp", "发布日期", "datetime", 18),
    ("data_encerramento_proposta", "投标截止", "datetime", 18),
    ("is_e_auction", "电子拍卖", "bool", 8),
    ("is_competitive_bid", "竞争性招标", "bool", 10),
    ("is_long_term_opportunity", "长期机会", "bool", 8),
    ("situacao_compra_nome", "状态", "text", 16),
    ("srp", "价格登记", "bool", 8),
]

_ITENS_COLS: list[ColumnDef] = [
    ("pncp_id", "PNCP编号", "text", 30),
    ("numero_item", "项号", "int", 6),
    ("descricao", "明细描述", "text", 55),
    ("material_ou_servico_nome", "物/服", "text", 8),
    ("categoria_item_catalogo", "品类(目录富化)", "text", 26),
    ("quantidade", "数量", "text", 10),
    ("unidade_medida", "单位", "text", 12),
    ("valor_unitario_estimado", "单价(BRL)", "money", 14),
    ("valor_total", "小计(BRL)", "money", 16),
    ("tipo_beneficio_nome", "优惠类型", "text", 24),
]

_CONTRATOS_COLS: list[ColumnDef] = [
    ("pncp_id", "合同编号", "text", 30),
    ("linked_contratacao_pncp_id", "对应招标", "text", 30),
    ("nome_razao_social_fornecedor", "中标供应商", "text", 36),
    ("ni_fornecedor", "供应商CNPJ/CPF", "text", 18),
    ("tipo_pessoa", "主体", "text", 6),
    ("valor_global", "合同金额(BRL)", "money", 16),
    ("data_assinatura", "签订日", "date", 12),
    ("data_vigencia_fim", "到期日", "date", 12),
    ("orgao_razao_social", "采购机构", "text", 32),
    ("uf_sigla", "州", "text", 6),
]

_PCA_COLS: list[ColumnDef] = [
    ("id_pca_pncp", "PCA编号", "text", 30),
    ("ano_pca", "计划年度", "int", 8),
    ("orgao_entidade_razao_social", "机构", "text", 32),
    ("descricao_item", "采购标的", "text", 50),
    ("categoria_item_pca_nome", "物/服", "text", 8),
    ("quantidade_estimada", "预估数量", "text", 10),
    ("valor_total", "预估金额(BRL)", "money", 16),
    ("data_desejada", "期望采购日", "date", 12),
    ("classificacao_superior_nome", "上级分类", "text", 28),
]

# 机构画像(P6 富化:聚合 contratacoes + 可选 BrasilAPI)
_ORGAOS_COLS: list[ColumnDef] = [
    ("cnpj", "机构CNPJ", "text", 18),
    ("nome", "机构名称", "text", 38),
    ("esfera", "级别", "text", 6),
    ("uf", "州", "text", 6),
    ("municipio", "城市", "text", 18),
    ("total_contratacoes_2y", "近2年招标数", "int", 12),
    ("total_valor_2y", "近2年累计额(BRL)", "money", 20),
    ("tier", "机构分层", "text", 10),
    ("last_active_date", "最近活跃日", "date", 14),
]


# ─── 主导出函数 ────────────────────────────────────────────────────────


def export_to_excel(out_path: str | Path, *, limit: int | None = None) -> dict[str, int]:
    """把数据库导成多 Sheet Excel。

    Args:
        out_path: 输出 ``.xlsx`` 路径。
        limit: 每张表最多导多少行(``None`` = 全部)。

    Returns:
        各 Sheet 行数统计。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dim_zh = _dimension_zh_map()

    def _transform(key: str, value: Any) -> Any:
        if key in ("dimension_primary",) and value:
            return dim_zh.get(value, value)
        if key == "dimension_secondary" and value:
            return [dim_zh.get(v, v) for v in value]
        if key in ("region_gdp_tier", "tier") and value:
            return _TIER_ZH.get(value, value)
        return value

    wb = Workbook(write_only=True)  # 流式写盘,不在内存堆 Cell(write_only 无默认 sheet)

    stats: dict[str, int] = {}

    with session_scope() as session:
        # 1. 招标主表(流式:只查需要的列 + 逐行写盘)
        stats["招标主表"] = _add_sheet(
            wb,
            "招标主表",
            _CONTRATACOES_COLS,
            _stream_columns(
                session,
                Contratacao,
                _CONTRATACOES_COLS,
                order_by=Contratacao.data_publicacao_pncp.desc(),
                limit=limit,
            ),
            value_transform=_transform,
        )

        # 2. 明细
        stats["采购明细"] = _add_sheet(
            wb,
            "采购明细",
            _ITENS_COLS,
            _stream_columns(session, Item, _ITENS_COLS, limit=limit),
        )

        # 3. 合同
        stats["已签合同"] = _add_sheet(
            wb,
            "已签合同",
            _CONTRATOS_COLS,
            _stream_columns(
                session, Contrato, _CONTRATOS_COLS, order_by=Contrato.valor_global.desc(), limit=limit
            ),
        )

        # 4. PCA
        stats["年度采购计划"] = _add_sheet(
            wb,
            "年度采购计划",
            _PCA_COLS,
            _stream_columns(
                session, PcaItem, _PCA_COLS, order_by=PcaItem.data_desejada, limit=limit
            ),
        )

        # 5. 机构画像(P6 富化:聚合 contratacoes + 可选 BrasilAPI)
        stats["机构画像"] = _add_sheet(
            wb,
            "机构画像",
            _ORGAOS_COLS,
            _stream_columns(
                session, Orgao, _ORGAOS_COLS, order_by=Orgao.total_valor_2y.desc(), limit=limit
            ),
            value_transform=_transform,
        )

        # 6. 维度透视(小,直接 list)
        pivot_rows = _build_pivot(session, dim_zh)
        stats["维度透视"] = _add_sheet(
            wb,
            "维度透视",
            [
                ("dimension", "维度", "text", 16),
                ("n", "招标数", "int", 10),
                ("total_valor", "总金额(BRL)", "money", 20),
                ("avg_valor", "平均金额(BRL)", "money", 20),
            ],
            pivot_rows,
        )

    # 7. 过滤审计(从 Parquet)
    filtered_rows = _read_filtered_log()
    stats["过滤审计"] = _add_sheet(
        wb,
        "过滤审计",
        [
            ("pncp_id", "PNCP编号", "text", 30),
            ("filter_rule_id", "命中规则", "text", 10),
            ("filter_reason", "过滤原因", "text", 36),
            ("modalidade_nome", "采购方式", "text", 18),
            ("filtered_at", "过滤时间", "text", 22),
        ],
        filtered_rows,
    )

    wb.save(out_path)
    logger.bind(path=str(out_path), **stats).info("exporter.excel.saved")
    return stats


def _build_pivot(session: Any, dim_zh: dict[str, str]) -> list[dict[str, Any]]:
    """按 dimension_primary 聚合招标数 + 金额。"""
    stmt = (
        select(
            Contratacao.dimension_primary,
            func.count().label("n"),
            func.sum(Contratacao.valor_total_estimado).label("total"),
        )
        .group_by(Contratacao.dimension_primary)
        .order_by(func.count().desc())
    )
    out: list[dict[str, Any]] = []
    for dim, n, total in session.execute(stmt):
        total = total or 0.0
        out.append(
            {
                "dimension": dim_zh.get(dim, dim) if dim else "(未分类)",
                "n": n,
                "total_valor": total,
                "avg_valor": (total / n) if n else 0.0,
            }
        )
    return out


def _read_filtered_log(root: Path | None = None) -> list[dict[str, Any]]:
    """读 filtered_log 所有 Parquet 分区,合并成行列表。"""
    if root is None:
        root = Path(get_filter_settings()["filtered_log_dir"])
    files = sorted(root.rglob("*.parquet")) if root.exists() else []
    if not files:
        return []
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return []

    rows: list[dict[str, Any]] = []
    for f in files:
        table = pq.read_table(
            f,
            columns=None,
        )
        for rec in table.to_pylist():
            rows.append(
                {
                    "pncp_id": rec.get("pncp_id"),
                    "filter_rule_id": rec.get("filter_rule_id"),
                    "filter_reason": rec.get("filter_reason"),
                    "modalidade_nome": rec.get("modalidade_nome"),
                    "filtered_at": rec.get("filtered_at"),
                }
            )
    return rows


__all__ = ["export_to_excel"]
