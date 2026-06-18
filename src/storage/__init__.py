"""存储层(storage)。

基于 SQLAlchemy 2.0,开发用 SQLite,生产可换 PostgreSQL。

模块:
    database.py     Engine / Session 工厂 + session_scope 事务上下文
    models.py       Contratacao / Item / TranslationCache / SyncCursor 四张表 ORM
    schemas.py      PublicacaoRaw(镜像 API) + ContratacaoIn(扁平入库) + Item*
    repositories.py ContratacaoRepository / ItemRepository / TranslationRepository(UPSERT 幂等)
    cursor.py       CursorRepository(增量游标)
"""
from .cursor import CursorRepository
from .database import get_engine, get_session_factory, init_db, session_scope
from .models import (
    Base,
    Contratacao,
    Item,
    SyncCursor,
    TranslationCache,
)
from .repositories import (
    ContratacaoRepository,
    ItemRepository,
    TranslationRepository,
)
from .schemas import (
    AmparoLegal,
    ContratacaoIn,
    ItemIn,
    ItemRaw,
    OrgaoEntidade,
    PublicacaoRaw,
    UnidadeOrgao,
)

__all__ = [
    "Base",
    "Contratacao",
    "Item",
    "TranslationCache",
    "SyncCursor",
    "AmparoLegal",
    "OrgaoEntidade",
    "UnidadeOrgao",
    "PublicacaoRaw",
    "ContratacaoIn",
    "ItemRaw",
    "ItemIn",
    "ContratacaoRepository",
    "ItemRepository",
    "TranslationRepository",
    "CursorRepository",
    "get_engine",
    "get_session_factory",
    "init_db",
    "session_scope",
]
