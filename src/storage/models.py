"""SQLAlchemy 2.0 ORM 模型。

四张表:

* :class:`Contratacao` — 招标主表(~50 列,字段基于 PNCP publicacao 实采)
* :class:`Item` — 招标明细(``pncp_itens`` fetcher 填)
* :class:`TranslationCache` — 葡→中标的梗概缓存
* :class:`SyncCursor` — 增量同步游标(每个 fetcher 一条)

字段命名:snake_case;入库前由 :mod:`src.storage.schemas` 把 PNCP API
的 camelCase 嵌套结构扁平化转过来。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


# ─── 招标主表 ──────────────────────────────────────────────────────────


class Contratacao(Base):
    """招标主表。

    主键 ``pncp_id`` = PNCP API 的 ``numeroControlePNCP``,长度固定 28 字符
    (例: ``01612441000107-1-000076/2026``)。

    P3 过滤层会写入 ``is_*`` / ``filter_*`` 字段。
    P5-P6 富化层会写入 ``region_*`` / ``dimension_*`` 字段。
    """

    __tablename__ = "contratacoes"

    # ─── 标识 ───
    pncp_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ano_compra: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequencial_compra: Mapped[int | None] = mapped_column(Integer, nullable=True)
    numero_compra: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processo: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ─── 描述(可能 > 600 字符,用 TEXT) ───
    objeto_compra: Mapped[str | None] = mapped_column(Text, nullable=True)
    informacao_complementar: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ─── 金额 ───
    valor_total_estimado: Mapped[float | None] = mapped_column(Float, nullable=True)
    valor_total_homologado: Mapped[float | None] = mapped_column(Float, nullable=True)
    valor_cny_estimado: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ─── 时间 ───
    data_publicacao_pncp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_abertura_proposta: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_encerramento_proposta: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_inclusao: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_atualizacao_global: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dias_restantes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ─── 采购方式 ───
    modalidade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    modalidade_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    modo_disputa_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    modo_disputa_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tipo_instrumento_codigo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tipo_instrumento_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    srp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    situacao_compra_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    situacao_compra_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ─── 法律依据 ───
    amparo_legal_codigo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amparo_legal_nome: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amparo_legal_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ─── 机构(orgaoEntidade) ───
    orgao_cnpj: Mapped[str | None] = mapped_column(String(20), nullable=True)
    orgao_razao_social: Mapped[str | None] = mapped_column(String(256), nullable=True)
    orgao_esfera_id: Mapped[str | None] = mapped_column(String(4), nullable=True)  # F/E/M/N
    orgao_poder_id: Mapped[str | None] = mapped_column(String(4), nullable=True)  # E/L/J/N

    # ─── 采购单位(unidadeOrgao) ───
    unidade_codigo_ibge: Mapped[str | None] = mapped_column(String(20), nullable=True)
    unidade_codigo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unidade_nome: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uf_sigla: Mapped[str | None] = mapped_column(String(4), nullable=True)
    uf_nome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    municipio_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # ─── 标注(P3 软标注) ───
    is_long_term_opportunity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_competitive_bid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_e_auction: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_me_epp_exclusivo: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ─── 过滤(软删除模式)───
    filter_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filter_rule_id: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ─── 富化(P5-P6) ───
    region_macro: Mapped[str | None] = mapped_column(String(16), nullable=True)
    region_gdp_tier: Mapped[str | None] = mapped_column(String(8), nullable=True)
    dimension_primary: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dimension_secondary: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    dimension_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimension_match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ─── 链接 / Edital PDF ───
    link_sistema_origem: Mapped[str | None] = mapped_column(String(512), nullable=True)
    edital_pdf_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    edital_pdf_extracted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edital_pdf_extracted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # PDF 解析出的全文(可能很长,一个 Edital 5-100K 字符);P3 二次过滤 F004-F006 的输入
    edital_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ─── 审计 ───
    crawl_timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ─── 查询索引 ───
    __table_args__ = (
        Index("ix_contratacoes_orgao_cnpj", "orgao_cnpj"),
        Index("ix_contratacoes_uf_sigla", "uf_sigla"),
        Index("ix_contratacoes_modalidade_id", "modalidade_id"),
        Index("ix_contratacoes_data_publicacao_pncp", "data_publicacao_pncp"),
        Index("ix_contratacoes_unidade_codigo_ibge", "unidade_codigo_ibge"),
        Index("ix_contratacoes_dimension_primary", "dimension_primary"),
    )

    def __repr__(self) -> str:
        return (
            f"<Contratacao {self.pncp_id} {self.modalidade_nome} "
            f"uf={self.uf_sigla} valor={self.valor_total_estimado}>"
        )


# ─── 招标明细 ──────────────────────────────────────────────────────────


class Item(Base):
    """招标明细。一个招标 N 个明细;主键 ``(pncp_id, numero_item)``。

    数据来源:``/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens``,
    走 :mod:`src.fetchers.pncp_itens`(普通 HttpClient,不被 F5 WAF 拦)。

    字段比原设计的 9 列扩展到 ~30 列 — 真实 API 返回了远多于设计文档的
    boolean 信号(``incentivo_produtivo_basico`` / ``tipo_beneficio`` /
    ``exigencia_conteudo_nacional``),P3 过滤层可以直接查这些字段,
    比解析 PDF 简单。
    """

    __tablename__ = "itens"

    # 复合主键
    pncp_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("contratacoes.pncp_id", ondelete="CASCADE"),
        primary_key=True,
    )
    numero_item: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 基本字段
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantidade: Mapped[float | None] = mapped_column(Float, nullable=True)
    unidade_medida: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valor_unitario_estimado: Mapped[float | None] = mapped_column(Float, nullable=True)
    valor_total: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 物品 / 服务大类(M / S)— 维度分类核心
    material_ou_servico: Mapped[str | None] = mapped_column(String(4), nullable=True)
    material_ou_servico_nome: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 类别
    item_categoria_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_categoria_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 评标
    criterio_julgamento_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criterio_julgamento_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 状态
    situacao_compra_item: Mapped[int | None] = mapped_column(Integer, nullable=True)
    situacao_compra_item_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tem_resultado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ME/EPP & 本地产品规则(直接的 boolean,P3 硬过滤要)
    tipo_beneficio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tipo_beneficio_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    incentivo_produtivo_basico: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exigencia_conteudo_nacional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 国货倾向(优先采购的百分比)
    aplicabilidade_margem_preferencia_normal: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    aplicabilidade_margem_preferencia_adicional: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    percentual_margem_preferencia_normal: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentual_margem_preferencia_adicional: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    tipo_margem_preferencia: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 编码
    ncm_nbs_codigo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ncm_nbs_descricao: Mapped[str | None] = mapped_column(String(256), nullable=True)
    catalogo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    categoria_item_catalogo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    catalogo_codigo_item: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 其它
    orcamento_sigiloso: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    informacao_complementar: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_inclusao: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 审计
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_itens_catalogo_codigo_item", "catalogo_codigo_item"),
        Index("ix_itens_ncm_nbs_codigo", "ncm_nbs_codigo"),
        Index("ix_itens_material_ou_servico", "material_ou_servico"),
    )

    def __repr__(self) -> str:
        desc = (self.descricao or "")[:40]
        return f"<Item {self.pncp_id}#{self.numero_item} {desc}>"


# ─── 已签合同 ──────────────────────────────────────────────────────────


# ─── 年度采购计划(PCA)── 来自 /api/consulta/v1/pca/* ────────────────


# ─── 机构画像 ──────────────────────────────────────────────────────────


# ─── Compras.gov.br 标准品类目录(CATMAT/CATSER 维表)──────────────────


# ─── 翻译缓存(葡→中标的梗概)───────────────────────────────────────────


class TranslationCache(Base):
    """葡语标的 → 中文译文 缓存(避免重复调翻译 API)。

    采购标的高度重复,按**原文 hash** 去重:同一段葡语只翻一次、永久复用。
    ``translate`` 命令填充本表(调 DeepSeek);导出时按 hash 查,命中直接用、
    未命中回退离线词典。UPSERT 幂等(主键 ``text_hash``)。
    """

    __tablename__ = "translation_cache"

    text_hash: Mapped[str] = mapped_column(String(40), primary_key=True)  # sha1(原文)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    translated: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(48), nullable=True)  # 译文来源:deepseek-v4-flash / offline
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<TranslationCache {self.text_hash[:8]} {self.model}>"


# ─── 增量游标 ──────────────────────────────────────────────────────────


class SyncCursor(Base):
    """增量同步游标。

    每个 fetcher 在表里保留一条记录,记录"上次拉到的最末日期"。
    fetcher 启动时读它确定 dataInicial(增量优先)。
    """

    __tablename__ = "sync_cursor"

    fetcher_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_data_inicial: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_data_final: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_run_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<SyncCursor {self.fetcher_name} last={self.last_data_final}>"


__all__ = [
    "Base",
    "Contratacao",
    "Item",
    "TranslationCache",
    "SyncCursor",
]
