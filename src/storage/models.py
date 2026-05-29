"""SQLAlchemy 2.0 ORM 模型。

四张表(见 CLAUDE.md §4 与 docs/02_字段拓展设计.md §5):

* :class:`Contratacao` — 招标主表(~50 列,字段基于 PNCP publicacao 实采)
* :class:`Item` — 招标明细(P1 补完 ``pncp_itens`` fetcher 后填)
* :class:`Orgao` — 采购机构画像(P5/P6 富化阶段填)
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

    字段比 docs/02 §5 的 9 列扩展到 ~30 列 — 真实 API 返回了远多于设计文档的
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


class Contrato(Base):
    """已签合同(Contrato)— 来自 ``/api/consulta/v1/contratos``。

    跟 :class:`Contratacao`(招标)是 N:1 关系:
    一个招标可以有 N 个合同(典型场景:分包给多个供应商);
    合同表里 ``linked_contratacao_pncp_id`` 字段(= API 的 ``numeroControlePncpCompra``)
    指回主表 ``contratacoes.pncp_id``。

    合同自己的 ``pncp_id`` 的 tipo 段是 ``2``(招标是 ``1``,合同是 ``2``,ATA 是别的)。
    """

    __tablename__ = "contratos"

    # ─── 标识 ───
    pncp_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    linked_contratacao_pncp_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    linked_ata_pncp_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ano_contrato: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequencial_contrato: Mapped[int | None] = mapped_column(Integer, nullable=True)
    numero_contrato_empenho: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processo: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ─── 类型 / 流程 ───
    tipo_contrato_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tipo_contrato_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    categoria_processo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    categoria_processo_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ─── 合同标的 ───
    objeto_contrato: Mapped[str | None] = mapped_column(Text, nullable=True)
    informacao_complementar: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ─── 金额 ───
    valor_inicial: Mapped[float | None] = mapped_column(Float, nullable=True)
    valor_global: Mapped[float | None] = mapped_column(Float, nullable=True)
    valor_parcela: Mapped[float | None] = mapped_column(Float, nullable=True)
    valor_acumulado: Mapped[float | None] = mapped_column(Float, nullable=True)
    numero_parcelas: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ─── 时间 ───
    data_assinatura: Mapped[date | None] = mapped_column(Date, nullable=True)
    data_vigencia_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    data_vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    data_publicacao_pncp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_atualizacao_global: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ─── 供应商(竞品分析核心)───
    ni_fornecedor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nome_razao_social_fornecedor: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tipo_pessoa: Mapped[str | None] = mapped_column(String(4), nullable=True)  # PJ / PF
    codigo_pais_fornecedor: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # ─── 分包 ───
    ni_fornecedor_sub_contratado: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nome_fornecedor_sub_contratado: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tipo_pessoa_sub_contratada: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # ─── 机构(orgaoEntidade) ───
    orgao_cnpj: Mapped[str | None] = mapped_column(String(20), nullable=True)
    orgao_razao_social: Mapped[str | None] = mapped_column(String(256), nullable=True)
    orgao_esfera_id: Mapped[str | None] = mapped_column(String(4), nullable=True)
    orgao_poder_id: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # ─── 采购单位 ───
    unidade_codigo_ibge: Mapped[str | None] = mapped_column(String(20), nullable=True)
    unidade_codigo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unidade_nome: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uf_sigla: Mapped[str | None] = mapped_column(String(4), nullable=True)
    uf_nome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    municipio_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # ─── 代理 / 转包(完整 dict 留 JSON) ───
    orgao_sub_rogado: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    unidade_sub_rogada: Mapped[Any | None] = mapped_column(JSON, nullable=True)

    # ─── 标志 ───
    receita: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fruto_adesao: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tem_remanejamento: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    numero_retificacao: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ─── CIPI / 议会修正案 ───
    identificador_cipi: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url_cipi: Mapped[str | None] = mapped_column(String(512), nullable=True)
    emenda_parlamentar: Mapped[Any | None] = mapped_column(JSON, nullable=True)

    # ─── 审计 ───
    usuario_nome: Mapped[str | None] = mapped_column(String(128), nullable=True)
    crawl_timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_contratos_linked_contratacao", "linked_contratacao_pncp_id"),
        Index("ix_contratos_ni_fornecedor", "ni_fornecedor"),
        Index("ix_contratos_orgao_cnpj", "orgao_cnpj"),
        Index("ix_contratos_uf_sigla", "uf_sigla"),
        Index("ix_contratos_data_assinatura", "data_assinatura"),
        Index("ix_contratos_data_publicacao_pncp", "data_publicacao_pncp"),
    )

    def __repr__(self) -> str:
        return (
            f"<Contrato {self.pncp_id} → {self.linked_contratacao_pncp_id} "
            f"fornecedor={self.nome_razao_social_fornecedor} valor={self.valor_global}>"
        )


# ─── 年度采购计划(PCA)── 来自 /api/consulta/v1/pca/* ────────────────


class PcaItem(Base):
    """年度采购计划明细(PCA Item)。

    数据源:``/api/consulta/v1/pca/atualizacao``(或 ``/v1/pca/``)。

    业务价值(docs/01):**提前 6-12 个月预知商机** — 每个机构每年初发布次年
    采购计划,业务方可以做销售管线前置。

    数据结构跟招标完全不同:API 返回的每条 record 是一个 "PCA 头 + N 个 itens"
    的嵌套结构。本表把每个 item 扁平化成一行,**头部信息(机构 / 年份 / 单位)
    冗余存**,避免 join。

    复合主键 ``(id_pca_pncp, numero_item)`` 保证 UPSERT 幂等。
    """

    __tablename__ = "pca_itens"

    # ─── 主键 ───
    id_pca_pncp: Mapped[str] = mapped_column(String(60), primary_key=True)
    numero_item: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ─── PCA 头部(冗余存) ───
    ano_pca: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codigo_unidade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nome_unidade: Mapped[str | None] = mapped_column(String(256), nullable=True)
    orgao_entidade_cnpj: Mapped[str | None] = mapped_column(String(20), nullable=True)
    orgao_entidade_razao_social: Mapped[str | None] = mapped_column(String(256), nullable=True)
    data_publicacao_pncp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_atualizacao_global_pca: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ─── Item 字段 ───
    codigo_item: Mapped[str | None] = mapped_column(String(64), nullable=True)
    descricao_item: Mapped[str | None] = mapped_column(Text, nullable=True)
    categoria_item_pca_nome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classificacao_catalogo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nome_classificacao_catalogo: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ─── 数量 / 单位 / 金额 ───
    quantidade_estimada: Mapped[float | None] = mapped_column(Float, nullable=True)
    unidade_fornecimento: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valor_unitario: Mapped[float | None] = mapped_column(Float, nullable=True)
    valor_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    valor_orcamento_exercicio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ─── 期望采购日(关键 — 未来时间)───
    data_desejada: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ─── 分类编码 ───
    pdm_codigo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pdm_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    classificacao_superior_codigo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    classificacao_superior_nome: Mapped[str | None] = mapped_column(String(256), nullable=True)
    grupo_contratacao_codigo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grupo_contratacao_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    unidade_requisitante: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # ─── 时间戳 ───
    data_inclusao: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ─── 审计 ───
    crawl_timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_pca_itens_ano_pca", "ano_pca"),
        Index("ix_pca_itens_orgao_cnpj", "orgao_entidade_cnpj"),
        Index("ix_pca_itens_data_desejada", "data_desejada"),
        Index("ix_pca_itens_classificacao_sup", "classificacao_superior_codigo"),
        Index("ix_pca_itens_pdm_codigo", "pdm_codigo"),
    )

    def __repr__(self) -> str:
        desc = (self.descricao_item or "")[:30]
        return (
            f"<PcaItem {self.id_pca_pncp}#{self.numero_item} "
            f"{desc} valor_total={self.valor_total}>"
        )


# ─── 机构画像 ──────────────────────────────────────────────────────────


class Orgao(Base):
    """采购机构画像(以 CNPJ 为键)。

    富化字段(``total_*`` / ``tier``)由 P6 富化层填,基于历史采购统计。
    """

    __tablename__ = "orgaos"

    cnpj: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str | None] = mapped_column(String(256), nullable=True)
    esfera: Mapped[str | None] = mapped_column(String(4), nullable=True)
    uf: Mapped[str | None] = mapped_column(String(4), nullable=True)
    municipio: Mapped[str | None] = mapped_column(String(120), nullable=True)
    total_contratacoes_2y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_valor_2y: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tier: Mapped[str | None] = mapped_column(String(8), nullable=True)

    def __repr__(self) -> str:
        return f"<Orgao {self.cnpj} {self.nome}>"


# ─── Compras.gov.br 标准品类目录(CATMAT/CATSER 维表)──────────────────


class CatalogoCompra(Base):
    """Compras.gov.br(dadosabertos)的标准品类目录维表 —— **富化层参考数据**。

    数据源:``dadosabertos.compras.gov.br``
        * ``tipo="material"``: CATMAT(``/modulo-material/4_consultarItemMaterial``)
        * ``tipo="servico"``:  CATSER(``/modulo-servico/6_consultarItemServico``)

    用途:把 ``itens.catalogo_codigo_item`` / ``pca_itens.codigo_item`` 这些**裸编码**
    映射成可读的「大类 / 类 / PDM + 描述」,供富化(填 ``itens.categoria_item_catalogo``)
    与维度分类用。这是 PNCP 没有、但 Compras.gov.br 开放的标准品类树。

    复合主键 ``(tipo, codigo)``:material 的 ``codigoItem`` 与 servico 的
    ``codigoServico`` 数值空间可能重叠,故用 ``tipo`` 区分。
    """

    __tablename__ = "catalogo_compras"

    tipo: Mapped[str] = mapped_column(String(8), primary_key=True)  # material | servico
    codigo: Mapped[str] = mapped_column(String(32), primary_key=True)

    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    codigo_grupo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nome_grupo: Mapped[str | None] = mapped_column(String(128), nullable=True)
    codigo_classe: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nome_classe: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # material 的 PDM(最细类目,如「CADEIRA ESCRITÓRIO」)/ servico 的 subclasse
    nome_pdm: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ncm: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 仅 material
    status_ativo: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    crawl_timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_catalogo_compras_codigo", "codigo"),
        Index("ix_catalogo_compras_nome_classe", "nome_classe"),
    )

    def __repr__(self) -> str:
        return f"<CatalogoCompra {self.tipo}:{self.codigo} {self.nome_classe}>"


# ─── 增量游标 ──────────────────────────────────────────────────────────


class SyncCursor(Base):
    """增量同步游标。

    每个 fetcher 在表里保留一条记录,记录"上次拉到的最末日期"。
    fetcher 启动时读它确定 dataInicial(CLAUDE.md §1 第 4 条:增量优先)。
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
    "Contrato",
    "PcaItem",
    "Orgao",
    "CatalogoCompra",
    "SyncCursor",
]
