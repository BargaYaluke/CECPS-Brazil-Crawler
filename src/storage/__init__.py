"""存储层(storage)。

基于 SQLAlchemy 2.0,开发用 SQLite,生产可换 PostgreSQL。

模块:
    database.py     Engine / Session 工厂 + session_scope 事务上下文
    models.py       Contratacao / Item / Orgao / SyncCursor 四张表 ORM
    schemas.py      PublicacaoRaw(镜像 API) + ContratacaoIn(扁平入库)
    repositories.py ContratacaoRepository(UPSERT 幂等)
    cursor.py       CursorRepository(增量游标)

字段定义见 docs/02_字段拓展设计.md §5。
"""
from .cursor import CursorRepository
from .database import get_engine, get_session_factory, init_db, session_scope
from .models import (
    Base,
    CatalogoCompra,
    Contratacao,
    Contrato,
    Item,
    Orgao,
    PcaItem,
    SyncCursor,
)
from .repositories import (
    CatalogoRepository,
    ContratacaoRepository,
    ContratoRepository,
    ItemRepository,
    PcaRepository,
)
from .schemas import (
    AmparoLegal,
    CatalogoCompraIn,
    CategoriaProcesso,
    ContratacaoIn,
    ContratoIn,
    ContratoRaw,
    ItemIn,
    ItemRaw,
    OrgaoEntidade,
    PcaItemIn,
    PcaItemInline,
    PcaRaw,
    PublicacaoRaw,
    TipoContrato,
    UnidadeOrgao,
    catalogo_in_from_material,
    catalogo_in_from_servico,
)

__all__ = [
    "Base",
    "Contratacao",
    "Contrato",
    "Item",
    "Orgao",
    "PcaItem",
    "CatalogoCompra",
    "SyncCursor",
    "AmparoLegal",
    "OrgaoEntidade",
    "UnidadeOrgao",
    "TipoContrato",
    "CategoriaProcesso",
    "PublicacaoRaw",
    "ContratacaoIn",
    "ItemRaw",
    "ItemIn",
    "ContratoRaw",
    "ContratoIn",
    "PcaItemInline",
    "PcaRaw",
    "PcaItemIn",
    "CatalogoCompraIn",
    "catalogo_in_from_material",
    "catalogo_in_from_servico",
    "ContratacaoRepository",
    "ItemRepository",
    "ContratoRepository",
    "PcaRepository",
    "CatalogoRepository",
    "CursorRepository",
    "get_engine",
    "get_session_factory",
    "init_db",
    "session_scope",
]
