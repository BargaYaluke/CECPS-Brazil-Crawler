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


# ─── 已签合同(Contrato)── 来自 /api/consulta/v1/contratos ────────────


class TipoContrato(BaseModel):
    """合同类型(嵌套对象)。"""

    model_config = ConfigDict(extra="allow")
    id: int | None = None
    nome: str | None = None


class CategoriaProcesso(BaseModel):
    """采购类别(嵌套对象)。"""

    model_config = ConfigDict(extra="allow")
    id: int | None = None
    nome: str | None = None


class ContratoRaw(BaseModel):
    """单条 ``/contratos`` 返回的 record。41 个字段,字段名跟 publicacao 完全不同。

    ``extra="allow"`` 兜底:API 后续加字段不会让 fetcher 崩。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # 标识
    numeroControlePNCP: str
    numeroControlePncpCompra: str | None = None  # 关联回原招标
    numeroControlePncpAta: str | None = None
    anoContrato: int | None = None
    sequencialContrato: int | None = None
    numeroContratoEmpenho: str | None = None
    processo: str | None = None

    # 类型 / 流程
    tipoContrato: TipoContrato | None = None
    categoriaProcesso: CategoriaProcesso | None = None

    # 合同标的
    objetoContrato: str | None = None
    informacaoComplementar: str | None = None

    # 金额
    valorInicial: float | None = None
    valorGlobal: float | None = None
    valorParcela: float | None = None
    valorAcumulado: float | None = None
    numeroParcelas: int | None = None

    # 时间(pydantic v2 auto-parse ISO 8601)
    dataAssinatura: date | None = None
    dataVigenciaInicio: date | None = None
    dataVigenciaFim: date | None = None
    dataPublicacaoPncp: datetime | None = None
    dataAtualizacao: datetime | None = None
    dataAtualizacaoGlobal: datetime | None = None

    # 供应商
    niFornecedor: str | None = None
    nomeRazaoSocialFornecedor: str | None = None
    tipoPessoa: str | None = None  # PJ / PF
    codigoPaisFornecedor: str | None = None  # BRA / etc

    # 分包
    niFornecedorSubContratado: str | None = None
    nomeFornecedorSubContratado: str | None = None
    tipoPessoaSubContratada: str | None = None

    # 嵌套对象 — 直接复用招标用的(同 schema)
    orgaoEntidade: OrgaoEntidade | None = None
    unidadeOrgao: UnidadeOrgao | None = None

    # 代理(嵌套结构未知 / 通常 null,直接当 dict 留)
    orgaoSubRogado: Any | None = None
    unidadeSubRogada: Any | None = None

    # 标志
    receita: bool = False
    frutoAdesao: bool = False
    temRemanejamento: bool = False
    numeroRetificacao: int = 0

    # CIPI / 议会修正
    identificadorCipi: str | None = None
    urlCipi: str | None = None
    emendaParlamentar: Any | None = None

    # 审计
    usuarioNome: str | None = None

    def to_contrato_in(self, raw_json: dict[str, Any] | None = None) -> "ContratoIn":
        """扁平化为 ContratoIn(snake_case)入库。"""
        tc = self.tipoContrato
        cp = self.categoriaProcesso
        org = self.orgaoEntidade
        uni = self.unidadeOrgao

        return ContratoIn(
            pncp_id=self.numeroControlePNCP,
            linked_contratacao_pncp_id=self.numeroControlePncpCompra,
            linked_ata_pncp_id=self.numeroControlePncpAta,
            ano_contrato=self.anoContrato,
            sequencial_contrato=self.sequencialContrato,
            numero_contrato_empenho=self.numeroContratoEmpenho,
            processo=self.processo,
            tipo_contrato_id=tc.id if tc else None,
            tipo_contrato_nome=tc.nome if tc else None,
            categoria_processo_id=cp.id if cp else None,
            categoria_processo_nome=cp.nome if cp else None,
            objeto_contrato=self.objetoContrato,
            informacao_complementar=self.informacaoComplementar,
            valor_inicial=self.valorInicial,
            valor_global=self.valorGlobal,
            valor_parcela=self.valorParcela,
            valor_acumulado=self.valorAcumulado,
            numero_parcelas=self.numeroParcelas,
            data_assinatura=self.dataAssinatura,
            data_vigencia_inicio=self.dataVigenciaInicio,
            data_vigencia_fim=self.dataVigenciaFim,
            data_publicacao_pncp=self.dataPublicacaoPncp,
            data_atualizacao=self.dataAtualizacao,
            data_atualizacao_global=self.dataAtualizacaoGlobal,
            ni_fornecedor=self.niFornecedor,
            nome_razao_social_fornecedor=self.nomeRazaoSocialFornecedor,
            tipo_pessoa=self.tipoPessoa,
            codigo_pais_fornecedor=self.codigoPaisFornecedor,
            ni_fornecedor_sub_contratado=self.niFornecedorSubContratado,
            nome_fornecedor_sub_contratado=self.nomeFornecedorSubContratado,
            tipo_pessoa_sub_contratada=self.tipoPessoaSubContratada,
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
            orgao_sub_rogado=self.orgaoSubRogado,
            unidade_sub_rogada=self.unidadeSubRogada,
            receita=self.receita,
            fruto_adesao=self.frutoAdesao,
            tem_remanejamento=self.temRemanejamento,
            numero_retificacao=self.numeroRetificacao,
            identificador_cipi=self.identificadorCipi,
            url_cipi=self.urlCipi,
            emenda_parlamentar=self.emendaParlamentar,
            usuario_nome=self.usuarioNome,
            raw_json=raw_json,
        )


class ContratoIn(BaseModel):
    """``contratos`` 表 UPSERT 用,字段对齐 :class:`src.storage.models.Contrato`。"""

    model_config = ConfigDict(extra="forbid")

    pncp_id: str
    linked_contratacao_pncp_id: str | None = None
    linked_ata_pncp_id: str | None = None
    ano_contrato: int | None = None
    sequencial_contrato: int | None = None
    numero_contrato_empenho: str | None = None
    processo: str | None = None

    tipo_contrato_id: int | None = None
    tipo_contrato_nome: str | None = None
    categoria_processo_id: int | None = None
    categoria_processo_nome: str | None = None

    objeto_contrato: str | None = None
    informacao_complementar: str | None = None

    valor_inicial: float | None = None
    valor_global: float | None = None
    valor_parcela: float | None = None
    valor_acumulado: float | None = None
    numero_parcelas: int | None = None

    data_assinatura: date | None = None
    data_vigencia_inicio: date | None = None
    data_vigencia_fim: date | None = None
    data_publicacao_pncp: datetime | None = None
    data_atualizacao: datetime | None = None
    data_atualizacao_global: datetime | None = None

    ni_fornecedor: str | None = None
    nome_razao_social_fornecedor: str | None = None
    tipo_pessoa: str | None = None
    codigo_pais_fornecedor: str | None = None

    ni_fornecedor_sub_contratado: str | None = None
    nome_fornecedor_sub_contratado: str | None = None
    tipo_pessoa_sub_contratada: str | None = None

    orgao_cnpj: str | None = None
    orgao_razao_social: str | None = None
    orgao_esfera_id: str | None = None
    orgao_poder_id: str | None = None

    unidade_codigo_ibge: str | None = None
    unidade_codigo: str | None = None
    unidade_nome: str | None = None
    uf_sigla: str | None = None
    uf_nome: str | None = None
    municipio_nome: str | None = None

    orgao_sub_rogado: Any | None = None
    unidade_sub_rogada: Any | None = None

    receita: bool = False
    fruto_adesao: bool = False
    tem_remanejamento: bool = False
    numero_retificacao: int = 0

    identificador_cipi: str | None = None
    url_cipi: str | None = None
    emenda_parlamentar: Any | None = None

    usuario_nome: str | None = None
    raw_json: dict[str, Any] | None = None


# ─── 年度采购计划(PCA)── 来自 /api/consulta/v1/pca/* ────────────────


class PcaItemInline(BaseModel):
    """PCA 内嵌的单条 item 字段(21 字段)。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    numeroItem: int
    codigoItem: str | None = None
    descricaoItem: str | None = None
    categoriaItemPcaNome: str | None = None
    classificacaoCatalogoId: int | None = None
    nomeClassificacaoCatalogo: str | None = None
    quantidadeEstimada: float | None = None
    unidadeFornecimento: str | None = None
    valorUnitario: float | None = None
    valorTotal: float | None = None
    valorOrcamentoExercicio: float | None = None
    dataDesejada: date | None = None
    pdmCodigo: str | None = None
    pdmDescricao: str | None = None
    classificacaoSuperiorCodigo: str | None = None
    classificacaoSuperiorNome: str | None = None
    grupoContratacaoCodigo: str | None = None
    grupoContratacaoNome: str | None = None
    unidadeRequisitante: str | None = None
    dataInclusao: datetime | None = None
    dataAtualizacao: datetime | None = None


class PcaRaw(BaseModel):
    """API 返回的单条 PCA(头部 9 字段 + 内嵌 itens 数组)。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    idPcaPncp: str
    anoPca: int | None = None
    codigoUnidade: str | None = None
    nomeUnidade: str | None = None
    orgaoEntidadeCnpj: str | None = None
    orgaoEntidadeRazaoSocial: str | None = None
    dataPublicacaoPNCP: datetime | None = None
    dataAtualizacaoGlobalPCA: datetime | None = None
    itens: list[PcaItemInline] = []

    def explode_items(self, raw_json: dict[str, Any] | None = None) -> list["PcaItemIn"]:
        """把 PCA 头 + N 个 item **拍扁**成 N 个 ``PcaItemIn``,头信息冗余复制。

        Args:
            raw_json: 整条 PCA 头部的原始 dict(同一条 PCA 的所有 item 共享同一份 raw_json)。

        Returns:
            N 个 :class:`PcaItemIn`,准备 UPSERT。
        """
        results: list[PcaItemIn] = []
        for item in self.itens:
            results.append(
                PcaItemIn(
                    id_pca_pncp=self.idPcaPncp,
                    numero_item=item.numeroItem,
                    ano_pca=self.anoPca,
                    codigo_unidade=self.codigoUnidade,
                    nome_unidade=self.nomeUnidade,
                    orgao_entidade_cnpj=self.orgaoEntidadeCnpj,
                    orgao_entidade_razao_social=self.orgaoEntidadeRazaoSocial,
                    data_publicacao_pncp=self.dataPublicacaoPNCP,
                    data_atualizacao_global_pca=self.dataAtualizacaoGlobalPCA,
                    codigo_item=item.codigoItem,
                    descricao_item=item.descricaoItem,
                    categoria_item_pca_nome=item.categoriaItemPcaNome,
                    classificacao_catalogo_id=item.classificacaoCatalogoId,
                    nome_classificacao_catalogo=item.nomeClassificacaoCatalogo,
                    quantidade_estimada=item.quantidadeEstimada,
                    unidade_fornecimento=item.unidadeFornecimento,
                    valor_unitario=item.valorUnitario,
                    valor_total=item.valorTotal,
                    valor_orcamento_exercicio=item.valorOrcamentoExercicio,
                    data_desejada=item.dataDesejada,
                    pdm_codigo=item.pdmCodigo,
                    pdm_descricao=item.pdmDescricao,
                    classificacao_superior_codigo=item.classificacaoSuperiorCodigo,
                    classificacao_superior_nome=item.classificacaoSuperiorNome,
                    grupo_contratacao_codigo=item.grupoContratacaoCodigo,
                    grupo_contratacao_nome=item.grupoContratacaoNome,
                    unidade_requisitante=item.unidadeRequisitante,
                    data_inclusao=item.dataInclusao,
                    data_atualizacao=item.dataAtualizacao,
                    raw_json=raw_json,
                )
            )
        return results


class PcaItemIn(BaseModel):
    """扁平化、入 ``pca_itens`` 表用。字段对齐 :class:`models.PcaItem`。"""

    model_config = ConfigDict(extra="forbid")

    id_pca_pncp: str
    numero_item: int

    # PCA 头部冗余
    ano_pca: int | None = None
    codigo_unidade: str | None = None
    nome_unidade: str | None = None
    orgao_entidade_cnpj: str | None = None
    orgao_entidade_razao_social: str | None = None
    data_publicacao_pncp: datetime | None = None
    data_atualizacao_global_pca: datetime | None = None

    # Item 字段
    codigo_item: str | None = None
    descricao_item: str | None = None
    categoria_item_pca_nome: str | None = None
    classificacao_catalogo_id: int | None = None
    nome_classificacao_catalogo: str | None = None
    quantidade_estimada: float | None = None
    unidade_fornecimento: str | None = None
    valor_unitario: float | None = None
    valor_total: float | None = None
    valor_orcamento_exercicio: float | None = None
    data_desejada: date | None = None
    pdm_codigo: str | None = None
    pdm_descricao: str | None = None
    classificacao_superior_codigo: str | None = None
    classificacao_superior_nome: str | None = None
    grupo_contratacao_codigo: str | None = None
    grupo_contratacao_nome: str | None = None
    unidade_requisitante: str | None = None
    data_inclusao: datetime | None = None
    data_atualizacao: datetime | None = None

    raw_json: dict[str, Any] | None = None


# ─── Compras.gov.br 目录(CATMAT/CATSER)── 富化层维表入库版 ────────────


class CatalogoCompraIn(BaseModel):
    """``catalogo_compras`` 入库版(扁平 snake_case,对齐 ORM)。

    用 :func:`catalogo_in_from_material` / :func:`catalogo_in_from_servico`
    从 Compras.gov.br 的 camelCase record 转过来。
    """

    model_config = ConfigDict(extra="ignore")

    tipo: str
    codigo: str
    descricao: str | None = None
    codigo_grupo: int | None = None
    nome_grupo: str | None = None
    codigo_classe: int | None = None
    nome_classe: str | None = None
    nome_pdm: str | None = None
    ncm: str | None = None
    status_ativo: bool | None = None
    raw_json: dict[str, Any] | None = None


def catalogo_in_from_material(
    rec: dict[str, Any], raw_json: dict[str, Any] | None = None
) -> CatalogoCompraIn:
    """CATMAT 物料 record(``/modulo-material/4_consultarItemMaterial``)→ 入库版。"""
    return CatalogoCompraIn(
        tipo="material",
        codigo=str(rec.get("codigoItem")),
        descricao=rec.get("descricaoItem"),
        codigo_grupo=rec.get("codigoGrupo"),
        nome_grupo=rec.get("nomeGrupo"),
        codigo_classe=rec.get("codigoClasse"),
        nome_classe=rec.get("nomeClasse"),
        nome_pdm=rec.get("nomePdm"),
        ncm=rec.get("codigo_ncm"),
        status_ativo=rec.get("statusItem"),
        raw_json=raw_json if raw_json is not None else rec,
    )


def catalogo_in_from_servico(
    rec: dict[str, Any], raw_json: dict[str, Any] | None = None
) -> CatalogoCompraIn:
    """CATSER 服务 record(``/modulo-servico/6_consultarItemServico``)→ 入库版。"""
    return CatalogoCompraIn(
        tipo="servico",
        codigo=str(rec.get("codigoServico")),
        descricao=rec.get("nomeServico"),
        codigo_grupo=rec.get("codigoGrupo"),
        nome_grupo=rec.get("nomeGrupo"),
        codigo_classe=rec.get("codigoClasse"),
        nome_classe=rec.get("nomeClasse"),
        nome_pdm=rec.get("nomeSubclasse"),
        ncm=None,
        status_ativo=rec.get("statusServico"),
        raw_json=raw_json if raw_json is not None else rec,
    )


__all__ = [
    "AmparoLegal",
    "OrgaoEntidade",
    "UnidadeOrgao",
    "PublicacaoRaw",
    "ContratacaoIn",
    "ItemRaw",
    "ItemIn",
    "TipoContrato",
    "CategoriaProcesso",
    "ContratoRaw",
    "ContratoIn",
    "PcaItemInline",
    "PcaRaw",
    "PcaItemIn",
    "CatalogoCompraIn",
    "catalogo_in_from_material",
    "catalogo_in_from_servico",
]
