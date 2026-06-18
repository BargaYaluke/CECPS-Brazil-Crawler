"""Pydantic v2 数据模式。

两层 schema:

* :class:`PublicacaoRaw` — 直接镜像 PNCP ``/contratacoes/publicacao``
  单条 record(camelCase 字段名,保留嵌套对象)。fetcher 拿到 dict 后
  喂给它做字段校验。

* :class:`ContratacaoIn` — 扁平化 + snake_case,字段跟
  :class:`src.storage.models.Contratacao` ORM 完全对齐,准备 UPSERT 入库。

转换:``PublicacaoRaw.to_contratacao_in(raw_json=...)``。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ─── 镜像 PNCP API 的嵌套对象 ───────────────────────────────────────────


class AmparoLegal(BaseModel):
    """法律依据(嵌套在 record 内)。"""

    model_config = ConfigDict(extra="allow")
    codigo: int | None = None
    nome: str | None = None
    descricao: str | None = None


class OrgaoEntidade(BaseModel):
    """采购机构(主体)。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    cnpj: str | None = None
    razaoSocial: str | None = Field(default=None, alias="razaoSocial")
    esferaId: str | None = None  # F=Federal / E=Estadual / M=Municipal / N=Nacional
    poderId: str | None = None  # E=Executivo / L=Legislativo / J=Judiciario / N=Nao se aplica


class UnidadeOrgao(BaseModel):
    """采购单位(子机构)。注意:实际 API **没有** CNPJ 字段,只有 codigoIbge / codigoUnidade。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    codigoUnidade: str | None = None
    nomeUnidade: str | None = None
    ufSigla: str | None = None
    ufNome: str | None = None
    municipioNome: str | None = None
    codigoIbge: str | None = None


# ─── 镜像整条 record ───────────────────────────────────────────────────


class PublicacaoRaw(BaseModel):
    """单条 ``/contratacoes/publicacao`` record。

    ``extra="allow"`` 兜底:API 未来加字段不会让 fetcher 崩。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # 标识
    numeroControlePNCP: str
    anoCompra: int | None = None
    sequencialCompra: int | None = None
    numeroCompra: str | None = None
    processo: str | None = None

    # 描述
    objetoCompra: str | None = None
    informacaoComplementar: str | None = None

    # 金额
    valorTotalEstimado: float | None = None
    valorTotalHomologado: float | None = None

    # 时间(pydantic v2 自动 parse ISO 8601 字符串)
    dataPublicacaoPncp: datetime | None = None
    dataAberturaProposta: datetime | None = None
    dataEncerramentoProposta: datetime | None = None
    dataInclusao: datetime | None = None
    dataAtualizacao: datetime | None = None
    dataAtualizacaoGlobal: datetime | None = None

    # 采购方式
    modalidadeId: int | None = None
    modalidadeNome: str | None = None
    modoDisputaId: int | None = None
    modoDisputaNome: str | None = None
    tipoInstrumentoConvocatorioCodigo: int | None = None
    tipoInstrumentoConvocatorioNome: str | None = None
    srp: bool = False
    situacaoCompraId: int | None = None
    situacaoCompraNome: str | None = None

    # 嵌套对象
    amparoLegal: AmparoLegal | None = None
    orgaoEntidade: OrgaoEntidade | None = None
    unidadeOrgao: UnidadeOrgao | None = None

    # 链接
    linkSistemaOrigem: str | None = None

    def to_contratacao_in(self, raw_json: dict[str, Any] | None = None) -> "ContratacaoIn":
        """把嵌套 / camelCase 的 raw 转成扁平 / snake_case 的入库版。

        Args:
            raw_json: 原始 API dict,会原样存进 ``contratacoes.raw_json`` 列做审计。
                推荐传入,这样未来字段漏了也能从 raw_json 回溯。
        """
        amp = self.amparoLegal
        org = self.orgaoEntidade
        uni = self.unidadeOrgao

        return ContratacaoIn(
            pncp_id=self.numeroControlePNCP,
            ano_compra=self.anoCompra,
            sequencial_compra=self.sequencialCompra,
            numero_compra=self.numeroCompra,
            processo=self.processo,
            objeto_compra=self.objetoCompra,
            informacao_complementar=self.informacaoComplementar,
            valor_total_estimado=self.valorTotalEstimado,
            valor_total_homologado=self.valorTotalHomologado,
            data_publicacao_pncp=self.dataPublicacaoPncp,
            data_abertura_proposta=self.dataAberturaProposta,
            data_encerramento_proposta=self.dataEncerramentoProposta,
            data_inclusao=self.dataInclusao,
            data_atualizacao=self.dataAtualizacao,
            data_atualizacao_global=self.dataAtualizacaoGlobal,
            modalidade_id=self.modalidadeId,
            modalidade_nome=self.modalidadeNome,
            modo_disputa_id=self.modoDisputaId,
            modo_disputa_nome=self.modoDisputaNome,
            tipo_instrumento_codigo=self.tipoInstrumentoConvocatorioCodigo,
            tipo_instrumento_nome=self.tipoInstrumentoConvocatorioNome,
            srp=self.srp,
            situacao_compra_id=self.situacaoCompraId,
            situacao_compra_nome=self.situacaoCompraNome,
            amparo_legal_codigo=amp.codigo if amp else None,
            amparo_legal_nome=amp.nome if amp else None,
            amparo_legal_descricao=amp.descricao if amp else None,
            orgao_cnpj=org.cnpj if org else None,
            orgao_razao_social=org.razaoSocial if org else None,
            orgao_esfera_id=org.esferaId if org else None,
            orgao_poder_id=org.poderId if org else None,
            unidade_codigo_ibge=uni.codigoIbge if uni else None,
            unidade_codigo=uni.codigoUnidade if uni else None,
            unidade_nome=uni.nomeUnidade if uni else None,
            uf_sigla=uni.ufSigla if uni else None,
            uf_nome=uni.ufNome if uni else None,
            municipio_nome=uni.municipioNome if uni else None,
            link_sistema_origem=self.linkSistemaOrigem,
            raw_json=raw_json,
        )


# ─── 扁平化、入库用 ────────────────────────────────────────────────────


class ContratacaoIn(BaseModel):
    """准备 UPSERT 进 ``contratacoes`` 表的扁平结构。

    字段名与 :class:`src.storage.models.Contratacao` ORM 严格一一对应。
    repository 直接 ``model_dump()`` 后喂 ``insert(...).on_conflict_do_update``。
    """

    model_config = ConfigDict(extra="forbid")

    # 标识
    pncp_id: str
    ano_compra: int | None = None
    sequencial_compra: int | None = None
    numero_compra: str | None = None
    processo: str | None = None

    # 描述
    objeto_compra: str | None = None
    informacao_complementar: str | None = None

    # 金额
    valor_total_estimado: float | None = None
    valor_total_homologado: float | None = None
    valor_cny_estimado: float | None = None

    # 时间
    data_publicacao_pncp: datetime | None = None
    data_abertura_proposta: datetime | None = None
    data_encerramento_proposta: datetime | None = None
    data_inclusao: datetime | None = None
    data_atualizacao: datetime | None = None
    data_atualizacao_global: datetime | None = None
    dias_restantes: int | None = None

    # 采购方式
    modalidade_id: int | None = None
    modalidade_nome: str | None = None
    modo_disputa_id: int | None = None
    modo_disputa_nome: str | None = None
    tipo_instrumento_codigo: int | None = None
    tipo_instrumento_nome: str | None = None
    srp: bool = False
    situacao_compra_id: int | None = None
    situacao_compra_nome: str | None = None

    # 法律
    amparo_legal_codigo: int | None = None
    amparo_legal_nome: str | None = None
    amparo_legal_descricao: str | None = None

    # 机构
    orgao_cnpj: str | None = None
    orgao_razao_social: str | None = None
    orgao_esfera_id: str | None = None
    orgao_poder_id: str | None = None

    # 采购单位
    unidade_codigo_ibge: str | None = None
    unidade_codigo: str | None = None
    unidade_nome: str | None = None
    uf_sigla: str | None = None
    uf_nome: str | None = None
    municipio_nome: str | None = None

    # 链接 / Edital PDF
    link_sistema_origem: str | None = None
    edital_pdf_url: str | None = None
    edital_pdf_extracted: bool = False
    edital_pdf_extracted_at: datetime | None = None
    edital_text: str | None = None

    # 标注(P3 软标注:T001 / T002 / T003 / ME-EPP 计算字段)
    is_long_term_opportunity: bool = False
    is_competitive_bid: bool = False
    is_e_auction: bool = False
    is_me_epp_exclusivo: bool | None = None

    # 过滤(soft_delete 模式下 P3 硬过滤命中也保留在主表,带这两个字段)
    filter_reason: str | None = None
    filter_rule_id: str | None = None

    # 审计 — raw_json 由调用方传(fetcher 拿到的原始 dict)
    raw_json: dict[str, Any] | None = None


# ─── 招标明细 ──────────────────────────────────────────────────────────


class ItemRaw(BaseModel):
    """单条 ``/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens`` 明细。

    PNCP API 返回 ~36 个字段;这里把对业务有用的都列出来,
    剩下的(``imagem`` / ``patrimonio`` / ``codigoRegistroImobiliario`` 等)
    走 ``extra="allow"`` 兜底,需要时再补字段。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # 基本
    numeroItem: int
    descricao: str | None = None
    quantidade: float | None = None
    unidadeMedida: str | None = None
    valorUnitarioEstimado: float | None = None
    valorTotal: float | None = None

    # 物品/服务
    materialOuServico: str | None = None
    materialOuServicoNome: str | None = None

    # 类别
    itemCategoriaId: int | None = None
    itemCategoriaNome: str | None = None

    # 评标
    criterioJulgamentoId: int | None = None
    criterioJulgamentoNome: str | None = None

    # 状态
    situacaoCompraItem: int | None = None
    situacaoCompraItemNome: str | None = None
    temResultado: bool = False

    # ME/EPP & 本地产品(P3 过滤的关键)
    tipoBeneficio: int | None = None
    tipoBeneficioNome: str | None = None
    incentivoProdutivoBasico: bool = False
    exigenciaConteudoNacional: bool = False

    # 国货倾向
    aplicabilidadeMargemPreferenciaNormal: bool = False
    aplicabilidadeMargemPreferenciaAdicional: bool = False
    percentualMargemPreferenciaNormal: float | None = None
    percentualMargemPreferenciaAdicional: float | None = None
    tipoMargemPreferencia: str | None = None

    # 编码
    ncmNbsCodigo: str | None = None
    ncmNbsDescricao: str | None = None
    catalogo: str | None = None
    categoriaItemCatalogo: str | None = None
    catalogoCodigoItem: str | None = None

    # 其它
    orcamentoSigiloso: bool = False
    informacaoComplementar: str | None = None
    dataInclusao: datetime | None = None
    dataAtualizacao: datetime | None = None

    def to_item_in(self, pncp_id: str, raw_json: dict[str, Any] | None = None) -> "ItemIn":
        """转为入库形态(snake_case + 注入 ``pncp_id`` FK)。"""
        return ItemIn(
            pncp_id=pncp_id,
            numero_item=self.numeroItem,
            descricao=self.descricao,
            quantidade=self.quantidade,
            unidade_medida=self.unidadeMedida,
            valor_unitario_estimado=self.valorUnitarioEstimado,
            valor_total=self.valorTotal,
            material_ou_servico=self.materialOuServico,
            material_ou_servico_nome=self.materialOuServicoNome,
            item_categoria_id=self.itemCategoriaId,
            item_categoria_nome=self.itemCategoriaNome,
            criterio_julgamento_id=self.criterioJulgamentoId,
            criterio_julgamento_nome=self.criterioJulgamentoNome,
            situacao_compra_item=self.situacaoCompraItem,
            situacao_compra_item_nome=self.situacaoCompraItemNome,
            tem_resultado=self.temResultado,
            tipo_beneficio=self.tipoBeneficio,
            tipo_beneficio_nome=self.tipoBeneficioNome,
            incentivo_produtivo_basico=self.incentivoProdutivoBasico,
            exigencia_conteudo_nacional=self.exigenciaConteudoNacional,
            aplicabilidade_margem_preferencia_normal=self.aplicabilidadeMargemPreferenciaNormal,
            aplicabilidade_margem_preferencia_adicional=self.aplicabilidadeMargemPreferenciaAdicional,
            percentual_margem_preferencia_normal=self.percentualMargemPreferenciaNormal,
            percentual_margem_preferencia_adicional=self.percentualMargemPreferenciaAdicional,
            tipo_margem_preferencia=self.tipoMargemPreferencia,
            ncm_nbs_codigo=self.ncmNbsCodigo,
            ncm_nbs_descricao=self.ncmNbsDescricao,
            catalogo=self.catalogo,
            categoria_item_catalogo=self.categoriaItemCatalogo,
            catalogo_codigo_item=self.catalogoCodigoItem,
            orcamento_sigiloso=self.orcamentoSigiloso,
            informacao_complementar=self.informacaoComplementar,
            data_inclusao=self.dataInclusao,
            data_atualizacao=self.dataAtualizacao,
            raw_json=raw_json,
        )


class ItemIn(BaseModel):
    """``itens`` 表 UPSERT 用,字段对齐 :class:`src.storage.models.Item`。"""

    model_config = ConfigDict(extra="forbid")

    pncp_id: str
    numero_item: int

    descricao: str | None = None
    quantidade: float | None = None
    unidade_medida: str | None = None
    valor_unitario_estimado: float | None = None
    valor_total: float | None = None

    material_ou_servico: str | None = None
    material_ou_servico_nome: str | None = None

    item_categoria_id: int | None = None
    item_categoria_nome: str | None = None

    criterio_julgamento_id: int | None = None
    criterio_julgamento_nome: str | None = None

    situacao_compra_item: int | None = None
    situacao_compra_item_nome: str | None = None
    tem_resultado: bool = False

    tipo_beneficio: int | None = None
    tipo_beneficio_nome: str | None = None
    incentivo_produtivo_basico: bool = False
    exigencia_conteudo_nacional: bool = False

    aplicabilidade_margem_preferencia_normal: bool = False
    aplicabilidade_margem_preferencia_adicional: bool = False
    percentual_margem_preferencia_normal: float | None = None
    percentual_margem_preferencia_adicional: float | None = None
    tipo_margem_preferencia: str | None = None

    ncm_nbs_codigo: str | None = None
    ncm_nbs_descricao: str | None = None
    catalogo: str | None = None
    categoria_item_catalogo: str | None = None
    catalogo_codigo_item: str | None = None

    orcamento_sigiloso: bool = False
    informacao_complementar: str | None = None
    data_inclusao: datetime | None = None
    data_atualizacao: datetime | None = None

    raw_json: dict[str, Any] | None = None


__all__ = [
    "AmparoLegal",
    "OrgaoEntidade",
    "UnidadeOrgao",
    "PublicacaoRaw",
    "ContratacaoIn",
    "ItemRaw",
    "ItemIn",
]
