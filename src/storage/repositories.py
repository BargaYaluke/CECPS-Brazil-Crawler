"""数据访问层(Repository pattern)。

提供 ``Contratacao`` 表的 UPSERT 与查询。

UPSERT 设计:以 ``pncp_id`` 为冲突键,**幂等可重入**
— 重复跑 fetcher,同一条 record 不会变成多行,而是字段被覆盖更新。

实现走 SQLite 方言的 ``INSERT ... ON CONFLICT DO UPDATE``。生产换 Postgres 时
另写一个 ``PostgresContratacaoRepository`` 即可,这里保持单一职责。
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..core.logger import logger
from .models import CatalogoCompra, Contratacao, Contrato, Item, PcaItem, TranslationCache
from .schemas import CatalogoCompraIn, ContratacaoIn, ContratoIn, ItemIn, PcaItemIn


class ContratacaoRepository:
    """``contratacoes`` 表的读写。

    Example::

        with session_scope() as session:
            repo = ContratacaoRepository(session)
            repo.upsert(contratacao_in)
            print(repo.count())
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ─── 写 ───────────────────────────────────────────────────────────

    def upsert(self, data: ContratacaoIn) -> None:
        """UPSERT 单条;以 ``pncp_id`` 为冲突键。

        命中冲突时,更新除主键外的所有字段(传入的 ``ContratacaoIn`` 里 None
        值会覆盖现有非 None — 这是有意的,反映 fetcher 最新看到的状态)。
        """
        values = data.model_dump()
        stmt = sqlite_insert(Contratacao).values(**values)
        update_cols = {k: getattr(stmt.excluded, k) for k in values if k != "pncp_id"}
        stmt = stmt.on_conflict_do_update(
            index_elements=["pncp_id"],
            set_=update_cols,
        )
        self._session.execute(stmt)

    def upsert_batch(self, items: Iterable[ContratacaoIn]) -> int:
        """批量 UPSERT,返回处理条数。flush 但不 commit(交给 ``session_scope``)。"""
        count = 0
        for item in items:
            self.upsert(item)
            count += 1
        self._session.flush()
        logger.bind(count=count).info("storage.upsert_batch.done")
        return count

    # ─── 读 ───────────────────────────────────────────────────────────

    def get(self, pncp_id: str) -> Contratacao | None:
        """主键查询。"""
        return self._session.get(Contratacao, pncp_id)

    def count(self) -> int:
        """全表条数。"""
        stmt = select(func.count()).select_from(Contratacao)
        return int(self._session.scalar(stmt) or 0)

    def list_by_modalidade(self, modalidade_id: int, limit: int = 100) -> list[Contratacao]:
        """按 modalidade 查询(限 ``limit`` 条),按发布日期降序。"""
        stmt = (
            select(Contratacao)
            .where(Contratacao.modalidade_id == modalidade_id)
            .order_by(Contratacao.data_publicacao_pncp.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))


class ItemRepository:
    """``itens`` 表的 UPSERT。

    冲突键: ``(pncp_id, numero_item)`` 复合主键。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, data: ItemIn) -> None:
        """UPSERT 单条明细。"""
        values = data.model_dump()
        stmt = sqlite_insert(Item).values(**values)
        # 冲突键之外的所有字段都覆盖更新
        update_cols = {
            k: getattr(stmt.excluded, k)
            for k in values
            if k not in ("pncp_id", "numero_item")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["pncp_id", "numero_item"],
            set_=update_cols,
        )
        self._session.execute(stmt)

    def upsert_batch(self, items: Iterable[ItemIn]) -> int:
        count = 0
        for item in items:
            self.upsert(item)
            count += 1
        self._session.flush()
        logger.bind(count=count).info("storage.items.upsert_batch.done")
        return count

    def count(self) -> int:
        stmt = select(func.count()).select_from(Item)
        return int(self._session.scalar(stmt) or 0)

    def count_by_pncp_id(self, pncp_id: str) -> int:
        """某个 contratacao 下的明细数(用于验证关联是否对)。"""
        stmt = select(func.count()).select_from(Item).where(Item.pncp_id == pncp_id)
        return int(self._session.scalar(stmt) or 0)

    def list_by_pncp_id(self, pncp_id: str) -> list[Item]:
        stmt = select(Item).where(Item.pncp_id == pncp_id).order_by(Item.numero_item)
        return list(self._session.scalars(stmt))


class ContratoRepository:
    """``contratos`` 表的 UPSERT 与查询。

    冲突键: ``pncp_id``(合同自己的 PNCP 控制号,tipo=2)。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, data: ContratoIn) -> None:
        """UPSERT 单条合同;以 ``pncp_id`` 为冲突键。"""
        values = data.model_dump()
        stmt = sqlite_insert(Contrato).values(**values)
        update_cols = {k: getattr(stmt.excluded, k) for k in values if k != "pncp_id"}
        stmt = stmt.on_conflict_do_update(
            index_elements=["pncp_id"],
            set_=update_cols,
        )
        self._session.execute(stmt)

    def upsert_batch(self, items: Iterable[ContratoIn]) -> int:
        count = 0
        for item in items:
            self.upsert(item)
            count += 1
        self._session.flush()
        logger.bind(count=count).info("storage.contratos.upsert_batch.done")
        return count

    def get(self, pncp_id: str) -> Contrato | None:
        return self._session.get(Contrato, pncp_id)

    def count(self) -> int:
        stmt = select(func.count()).select_from(Contrato)
        return int(self._session.scalar(stmt) or 0)

    def list_by_fornecedor(self, ni_fornecedor: str, limit: int = 100) -> list[Contrato]:
        """按供应商 CNPJ 查最近合同(竞品分析常用)。"""
        stmt = (
            select(Contrato)
            .where(Contrato.ni_fornecedor == ni_fornecedor)
            .order_by(Contrato.data_assinatura.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def list_by_contratacao(self, contratacao_pncp_id: str) -> list[Contrato]:
        """按招标 pncp_id 查该招标产生的所有合同(一个招标可分包多家)。"""
        stmt = (
            select(Contrato)
            .where(Contrato.linked_contratacao_pncp_id == contratacao_pncp_id)
            .order_by(Contrato.sequencial_contrato)
        )
        return list(self._session.scalars(stmt))


class PcaRepository:
    """``pca_itens`` 表的 UPSERT 与查询。

    冲突键: ``(id_pca_pncp, numero_item)`` 复合主键。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, data: PcaItemIn) -> None:
        """UPSERT 单条 PCA 明细。"""
        values = data.model_dump()
        stmt = sqlite_insert(PcaItem).values(**values)
        update_cols = {
            k: getattr(stmt.excluded, k)
            for k in values
            if k not in ("id_pca_pncp", "numero_item")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["id_pca_pncp", "numero_item"],
            set_=update_cols,
        )
        self._session.execute(stmt)

    def upsert_batch(self, items: Iterable[PcaItemIn]) -> int:
        count = 0
        for item in items:
            self.upsert(item)
            count += 1
        self._session.flush()
        logger.bind(count=count).info("storage.pca.upsert_batch.done")
        return count

    def count(self) -> int:
        stmt = select(func.count()).select_from(PcaItem)
        return int(self._session.scalar(stmt) or 0)

    def count_distinct_pca(self) -> int:
        """有多少个不同的 PCA(去重 id_pca_pncp)。"""
        stmt = select(func.count(func.distinct(PcaItem.id_pca_pncp)))
        return int(self._session.scalar(stmt) or 0)

    def list_by_pca(self, id_pca_pncp: str) -> list[PcaItem]:
        """某 PCA 下所有 itens(按 numero_item 升序)。"""
        stmt = (
            select(PcaItem)
            .where(PcaItem.id_pca_pncp == id_pca_pncp)
            .order_by(PcaItem.numero_item)
        )
        return list(self._session.scalars(stmt))

    def list_by_orgao(self, cnpj: str, ano: int | None = None, limit: int = 100) -> list[PcaItem]:
        """某机构(可选限年份)的 PCA 明细 — 业务方"看这个客户明年要买什么"。"""
        stmt = select(PcaItem).where(PcaItem.orgao_entidade_cnpj == cnpj)
        if ano is not None:
            stmt = stmt.where(PcaItem.ano_pca == ano)
        stmt = stmt.order_by(PcaItem.data_desejada).limit(limit)
        return list(self._session.scalars(stmt))


class CatalogoRepository:
    """``catalogo_compras`` 维表的 UPSERT 与查询(富化层用)。

    冲突键: ``(tipo, codigo)`` 复合主键。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, data: CatalogoCompraIn) -> None:
        """UPSERT 单条目录项。"""
        values = data.model_dump()
        stmt = sqlite_insert(CatalogoCompra).values(**values)
        update_cols = {
            k: getattr(stmt.excluded, k) for k in values if k not in ("tipo", "codigo")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["tipo", "codigo"],
            set_=update_cols,
        )
        self._session.execute(stmt)

    def upsert_batch(self, items: Iterable[CatalogoCompraIn]) -> int:
        count = 0
        for item in items:
            self.upsert(item)
            count += 1
        self._session.flush()
        logger.bind(count=count).info("storage.catalogo.upsert_batch.done")
        return count

    def count(self) -> int:
        stmt = select(func.count()).select_from(CatalogoCompra)
        return int(self._session.scalar(stmt) or 0)

    def get(self, tipo: str, codigo: str) -> CatalogoCompra | None:
        return self._session.get(CatalogoCompra, (tipo, codigo))

    def get_by_codigo(self, codigo: str, tipo: str | None = None) -> CatalogoCompra | None:
        """按 ``codigo`` 查目录项(富化 itens 用)。

        Args:
            codigo: 目录编码(= ``itens.catalogo_codigo_item``)。
            tipo: 可选限定 ``material`` / ``servico``;``None`` 时优先 material。
        """
        stmt = select(CatalogoCompra).where(CatalogoCompra.codigo == str(codigo))
        if tipo is not None:
            stmt = stmt.where(CatalogoCompra.tipo == tipo)
        else:
            stmt = stmt.order_by(CatalogoCompra.tipo)  # material 在 servico 前
        return self._session.scalars(stmt.limit(1)).first()


class TranslationRepository:
    """``translation_cache`` 表:葡→中译文按原文 hash 缓存。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, text_hash: str, source_text: str, translated: str, model: str) -> None:
        """UPSERT 单条译文(冲突键 ``text_hash``)。"""
        values = {
            "text_hash": text_hash,
            "source_text": source_text,
            "translated": translated,
            "model": model,
        }
        stmt = sqlite_insert(TranslationCache).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["text_hash"],
            set_={k: getattr(stmt.excluded, k) for k in values if k != "text_hash"},
        )
        self._session.execute(stmt)

    def existing_hashes(self) -> set[str]:
        """已缓存的 text_hash 集合(用于跳过已翻译的)。"""
        return set(self._session.scalars(select(TranslationCache.text_hash)))

    def api_hashes(self) -> set[str]:
        """**API 成功翻译**(model != offline)的 text_hash 集合。

        run_translate 用它做跳过集:API 译文跳过,但 offline 兜底的下次仍会重试升级。
        """
        return set(
            self._session.scalars(
                select(TranslationCache.text_hash).where(TranslationCache.model != "offline")
            )
        )

    def load_map(self) -> dict[str, str]:
        """返回 ``{text_hash: translated}`` 全量映射(导出时一次性载入查表)。"""
        rows = self._session.execute(
            select(TranslationCache.text_hash, TranslationCache.translated)
        )
        return {h: t for h, t in rows if t}

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(TranslationCache)) or 0)


__all__ = [
    "ContratacaoRepository",
    "ItemRepository",
    "ContratoRepository",
    "PcaRepository",
    "CatalogoRepository",
    "TranslationRepository",
]
