"""Excel 多 Sheet 报告导出(openpyxl)。

把 SQLite 里的招标 / 明细 / 合同 / 年度计划 + 维度透视 + 过滤审计
导成一份业务方能直接打开分析的 ``.xlsx``。

Sheet 结构(P6 富化后扩到 7 个 Sheet):
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
from ..enrichers.deadline import classify_deadline, status_zh
from ..enrichers.translate import text_hash, translate_objeto
from ..storage import (
    Contratacao,
    Contrato,
    Item,
    Orgao,
    PcaItem,
    TranslationRepository,
    session_scope,
)

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
    extra_keys: tuple[str, ...] = (),
    compute: Callable[[dict[str, Any]], None] | None = None,
    yield_size: int = 2000,
) -> Iterator[dict[str, Any]]:
    """**只查需要的真实列**(避开 raw_json 大字段)+ ``yield_per`` 流式产出行 dict。

    这是导出不撑爆内存的关键:``select(Model)`` 会加载完整 ORM 对象(含每行几 KB 的
    ``raw_json`` 原始留底),百万行级直接 OOM;这里只 SELECT 需要的列,逐批从游标取。

    Args:
        columns: 显示列定义。其中**真实 ORM 列**会被 SELECT;**计算列**(非 ORM 列名,
            如"标的中文梗概/时效状态")跳过 SELECT,交给 ``compute`` 回调填。
        extra_keys: 额外 SELECT 的 ORM 列(供 ``compute`` 用但不显示,如 orgao_cnpj)。
        compute: ``(row_dict) -> None``,就地给 row 填计算列。
    """
    table_cols = model.__table__.columns
    real_keys = [k for (k, _h, _kk, _w) in columns if k in table_cols]
    for ek in extra_keys:
        if ek not in real_keys:
            real_keys.append(ek)

    stmt = select(*[getattr(model, k) for k in real_keys])
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    if limit:
        stmt = stmt.limit(limit)
    for row in session.execute(stmt).yield_per(yield_size):
        d = dict(zip(real_keys, row))
        if compute is not None:
            compute(d)
        yield d


# ─── 各 Sheet 列定义 ───────────────────────────────────────────────────

_CONTRATACOES_COLS: list[ColumnDef] = [
    ("pncp_id", "PNCP编号", "text", 30),
    ("modalidade_nome", "采购方式", "text", 18),
    ("dimension_primary", "主维度", "text", 12),
    ("dimension_secondary", "次维度", "list", 16),
    ("dimension_confidence", "维度置信度", "text", 10),
    ("objeto_compra", "标的(原文)", "text", 50),
    ("objeto_zh", "标的(中文梗概)", "text", 50),
    ("valor_total_estimado", "预估金额(BRL)", "money", 16),
    ("valor_cny_estimado", "预估金额(CNY)", "money", 16),
    ("orgao_razao_social", "采购机构", "text", 32),
    ("orgao_buys_foreign", "采购方曾买外企", "text", 12),
    ("orgao_esfera_id", "级别", "text", 6),
    ("uf_sigla", "州", "text", 6),
    ("municipio_nome", "城市", "text", 18),
    ("region_macro", "大区", "text", 12),
    ("region_gdp_tier", "GDP分层", "text", 8),
    ("data_publicacao_pncp", "发布日期", "datetime", 18),
    ("data_encerramento_proposta", "投标截止", "datetime", 18),
    ("prazo_status", "时效状态", "text", 10),
    ("dias_restantes", "剩余天数", "int", 8),
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


def export_to_excel(
    out_path: str | Path, *, limit: int | None = None, hide_expired: bool = False
) -> dict[str, int]:
    """把数据库导成多 Sheet Excel。

    Args:
        out_path: 输出 ``.xlsx`` 路径。
        limit: 每张表最多导多少行(``None`` = 全部)。
        hide_expired: True 则招标主表里**隐藏已过期**(投标截止 < 今天)的标的。

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
    today = date.today()

    with session_scope() as session:
        # Q6: 先聚合"亲外企机构"(给招标主表打标注 + 单独 Sheet)
        foreign_sheet_rows, foreign_cnpjs = _foreign_orgaos(session)
        # Q4: 一次性载入翻译缓存({hash: 中文}),命中用 DeepSeek 译文,未命中回退离线词典
        translation_map = TranslationRepository(session).load_map()

        def _compute_contratacao(row: dict[str, Any]) -> None:
            """填招标主表的计算列:翻译梗概 / 时效 / 采购方亲外企标注。"""
            obj = row.get("objeto_compra")
            zh = translation_map.get(text_hash(obj)) if obj else None
            row["objeto_zh"] = zh or translate_objeto(obj)
            st, dias = classify_deadline(row.get("data_encerramento_proposta"), today)
            row["prazo_status"] = status_zh(st)
            row["dias_restantes"] = dias
            cnpj = row.get("orgao_cnpj")
            row["orgao_buys_foreign"] = "是" if cnpj in foreign_cnpjs else ""

        # 1. 招标主表(流式:只查需要的列 + 逐行写盘 + 计算列)
        ctr_rows: Iterable[dict[str, Any]] = _stream_columns(
            session,
            Contratacao,
            _CONTRATACOES_COLS,
            order_by=Contratacao.data_publicacao_pncp.desc(),
            limit=limit,
            extra_keys=("orgao_cnpj",),
            compute=_compute_contratacao,
        )
        if hide_expired:
            ctr_rows = (r for r in ctr_rows if r.get("prazo_status") != status_zh("expired"))
        stats["招标主表"] = _add_sheet(
            wb, "招标主表", _CONTRATACOES_COLS, ctr_rows, value_transform=_transform
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

        # 7. 亲外企机构(Q6:历史合同里给过外企的采购方)
        stats["亲外企机构"] = _add_sheet(
            wb,
            "亲外企机构",
            _FOREIGN_ORGAO_COLS,
            foreign_sheet_rows,
        )

    # 8. 过滤审计(从 Parquet)
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

    # 9. 字段说明(Q5:非常规字段释义,放最后)
    stats["字段说明"] = _add_sheet(
        wb,
        "字段说明",
        [("field", "字段", "text", 22), ("desc", "含义说明", "text", 90)],
        ({"field": f, "desc": d} for f, d in _GLOSSARY),
    )

    wb.save(out_path)
    logger.bind(path=str(out_path), **stats).info("exporter.excel.saved")
    return stats


# ─── Q6 亲外企机构 ─────────────────────────────────────────────────────

_FOREIGN_ORGAO_COLS: list[ColumnDef] = [
    ("cnpj", "机构CNPJ", "text", 18),
    ("nome", "机构名称", "text", 38),
    ("foreign_awards", "外企中标笔数", "int", 12),
    ("total_awards", "总中标笔数", "int", 12),
    ("foreign_ratio_pct", "外企占比%", "text", 10),
    ("foreign_valor", "外企合同额(BRL)", "money", 20),
    ("countries", "涉及国家", "text", 18),
    ("sample_vendor", "外企样例", "text", 36),
]


def _foreign_orgaos(session: Any) -> tuple[list[dict[str, Any]], set[str]]:
    """聚合 contratos:哪些采购方给过**外企**(codigo_pais_fornecedor 非 BRA 非空)。

    Returns:
        ``(sheet_rows, foreign_cnpj_set)`` —— sheet_rows 按外企笔数降序;
        foreign_cnpj_set 是给过外企的机构 CNPJ 集合(用于招标主表打标注)。

    Note:
        当前 contratos 样本里外企中标很稀有(一周仅十几笔),所以排行可能很短;
        随着 ``fetch-contratos`` 拉更长时间范围,这个清单会越来越有代表性。
    """
    stmt = select(
        Contrato.orgao_cnpj,
        Contrato.orgao_razao_social,
        Contrato.codigo_pais_fornecedor,
        Contrato.nome_razao_social_fornecedor,
        Contrato.valor_global,
    ).where(Contrato.orgao_cnpj.is_not(None))

    agg: dict[str, dict[str, Any]] = {}
    for cnpj, nome, pais, vendor, valor in session.execute(stmt).yield_per(5000):
        a = agg.get(cnpj)
        if a is None:
            a = agg[cnpj] = {
                "cnpj": cnpj,
                "nome": nome,
                "total_awards": 0,
                "foreign_awards": 0,
                "foreign_valor": 0.0,
                "_countries": set(),
                "sample_vendor": None,
            }
        a["total_awards"] += 1
        if nome and not a["nome"]:
            a["nome"] = nome
        is_foreign = pais is not None and pais != "BRA"
        if is_foreign:
            a["foreign_awards"] += 1
            a["foreign_valor"] += float(valor or 0.0)
            a["_countries"].add(pais)
            if not a["sample_vendor"] and vendor:
                a["sample_vendor"] = vendor

    foreign_cnpjs = {c for c, a in agg.items() if a["foreign_awards"] > 0}
    rows: list[dict[str, Any]] = []
    for a in agg.values():
        if a["foreign_awards"] == 0:
            continue
        ratio = 100.0 * a["foreign_awards"] / a["total_awards"] if a["total_awards"] else 0.0
        rows.append(
            {
                "cnpj": a["cnpj"],
                "nome": a["nome"],
                "foreign_awards": a["foreign_awards"],
                "total_awards": a["total_awards"],
                "foreign_ratio_pct": f"{ratio:.1f}",
                "foreign_valor": round(a["foreign_valor"], 2),
                "countries": ", ".join(sorted(a["_countries"])),
                "sample_vendor": a["sample_vendor"],
            }
        )
    rows.sort(key=lambda r: (-r["foreign_awards"], -r["foreign_valor"]))
    return rows, foreign_cnpjs


# ─── Q5 字段说明(术语释义)─────────────────────────────────────────────

_GLOSSARY: list[tuple[str, str]] = [
    ("标的(原文)", "PNCP 原始葡语标的描述(objetoCompra)。"),
    ("标的(中文梗概)", "离线词典把葡语标的翻成的中文梗概,用于快速判断这是什么标;残留专有名词保留葡语原文。"),
    ("主维度 / 次维度", "六大业务维度自动分类:数字经济 / 医疗医药 / 高端制造 / 大宗商贸 / 跨境电商 / 文化体育。次维度是同时命中的其它维度。"),
    ("维度置信度", "分类匹配的把握程度(0~1)。越高越可信;偏低建议人工复核。"),
    ("预估金额(CNY)", "预估金额(BRL)按当日 BRL→CNY 汇率(AwesomeAPI)折算的人民币参考值。"),
    ("采购方曾买外企", "「是」= 该采购机构在历史合同里给过非巴西供应商中标(见『亲外企机构』页)。中企可优先关注 —— 这类机构对外企接受度更高。"),
    ("级别", "采购机构行政层级:F=联邦 / E=州 / M=市 / N=不适用。"),
    ("大区 / GDP分层", "按州映射的 IBGE 五大区(北/东北/中西/东南/南)与经济分层(高/中/低)。"),
    ("发布日期", "登记到 PNCP 中央门户的时间。注意:可能晚于实际投标窗口(机构常追溯补登历史采购),所以判断能否投标看『投标截止』而非发布日。"),
    ("投标截止", "实际投标截止时间(dataEncerramentoProposta)。2099 表示无截止/常年开放。"),
    ("时效状态", "已过期=截止<今天(不能投);临近截止=≤5天内截止(可能来不及);还能投=尚有余量;无截止=常年开放;未知=截止日缺失。"),
    ("剩余天数", "距投标截止的天数。负数=已过期。"),
    ("电子拍卖", "T003:Pregão Eletrônico,巴西最常见的电子反向竞价(买方压价),透明度高、对外企友好。"),
    ("竞争性招标", "T002:Concorrência,公开竞争性招标,通常金额较大、流程正式。"),
    ("长期机会", "T001:Credenciamento(认证入库制)—— 不是一次性招标,而是持续登记合格供应商,长期有效。"),
    ("价格登记", "SRP / Registro de Preços:框架协议,中标后在有效期内按需下单,体量大。"),
    ("机构CNPJ", "采购机构的巴西法人税号(类似统一社会信用代码),全国唯一,用于跨表关联机构。"),
    ("机构分层(机构画像页)", "按近 2 年累计采购额分层:高(≥5000万)/中(≥500万)/低 BRL。"),
    ("外企占比%(亲外企页)", "该机构外企中标笔数 ÷ 总中标笔数。占比越高,对外企越开放。"),
]


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
