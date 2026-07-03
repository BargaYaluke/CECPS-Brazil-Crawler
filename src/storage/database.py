"""SQLAlchemy 2.0 engine 与 session 工厂。

从 ``config/settings.yaml`` 的 ``storage.database_url`` 读连接串
(默认 ``sqlite:///./data/procurement.db``);测试时显式传内存 DSN。

事务约定:写操作走 :func:`session_scope`,
异常自动 rollback。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.logger import logger
from ..core.settings import load_settings

_DEFAULT_DB_URL = "sqlite:///./data/procurement.db"


@lru_cache(maxsize=4)
def get_engine(database_url: str | None = None) -> Engine:
    """返回(按 URL 缓存的)Engine 单例。

    Args:
        database_url: 显式覆盖 ``settings.yaml``。仅测试时用
            ``sqlite:///:memory:``;不传则走配置。

    Returns:
        SQLAlchemy ``Engine``。
    """
    if database_url is None:
        try:
            storage_cfg = load_settings().get("storage", {}) or {}
        except Exception:
            storage_cfg = {}
        database_url = storage_cfg.get("database_url", _DEFAULT_DB_URL)

    # SQLite 文件路径:确保父目录存在(不存在 SQLAlchemy 报 unable to open)
    if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
        db_path = Path(database_url[len("sqlite:///") :])
        if db_path.parent and not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)

    is_sqlite_file = database_url.startswith("sqlite:///") and ":memory:" not in database_url
    # SQLite 默认 busy_timeout=0 — 一遇锁立刻 "database is locked"。
    # 给 30s 等待 + WAL,让短暂并发(读写交叠)排队而非直接崩。
    connect_args = {"timeout": 30} if database_url.startswith("sqlite") else {}
    engine = create_engine(
        database_url, future=True, echo=False, connect_args=connect_args
    )

    if is_sqlite_file:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _conn_record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")     # 读写不互锁
            cur.execute("PRAGMA busy_timeout=30000")   # 遇锁等待 30s 再放弃
            cur.execute("PRAGMA synchronous=NORMAL")   # WAL 下安全且更快
            cur.close()

    logger.bind(database_url=database_url).info("storage.engine.created")
    return engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """返回 ``sessionmaker``。"""
    if engine is None:
        engine = get_engine()
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """事务作用域 — 退出时 commit;异常时 rollback。

    Example::

        with session_scope() as session:
            repo = ContratacaoRepository(session)
            repo.upsert(...)
    """
    SessionLocal = get_session_factory(engine)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine | None = None) -> None:
    """create_all 建所有表。

    开发与测试用;生产应改走 Alembic 迁移。
    """
    if engine is None:
        engine = get_engine()
    from .models import Base  # 避免循环 import

    Base.metadata.create_all(bind=engine)
    logger.info("storage.db.initialized")


__all__ = ["get_engine", "get_session_factory", "session_scope", "init_db"]
