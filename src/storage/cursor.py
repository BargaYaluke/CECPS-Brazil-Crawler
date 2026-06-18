"""增量同步游标(``sync_cursor`` 表)的读写。

每个 fetcher 维护一条:启动时 ``get(fetcher_name)`` 拿到 ``last_data_final``,
推断本轮的 ``dataInicial = last_data_final + 1``;成功跑完后 ``update``。

约定:增量优先,全量爬取必须显式 --full。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..core.logger import logger
from .models import SyncCursor


class CursorRepository:
    """``sync_cursor`` 表的 UPSERT 与查询。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, fetcher_name: str) -> SyncCursor | None:
        """按 fetcher 名取游标;不存在返回 ``None``(意味着首次同步,跑全量)。"""
        return self._session.get(SyncCursor, fetcher_name)

    def update(
        self,
        fetcher_name: str,
        data_inicial: date,
        data_final: date,
    ) -> None:
        """UPSERT 游标 — 记录本轮抓的日期范围与时间。"""
        stmt = sqlite_insert(SyncCursor).values(
            fetcher_name=fetcher_name,
            last_data_inicial=data_inicial,
            last_data_final=data_final,
            last_run_at=datetime.utcnow(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["fetcher_name"],
            set_={
                "last_data_inicial": stmt.excluded.last_data_inicial,
                "last_data_final": stmt.excluded.last_data_final,
                "last_run_at": stmt.excluded.last_run_at,
            },
        )
        self._session.execute(stmt)
        logger.bind(
            fetcher_name=fetcher_name,
            data_inicial=data_inicial.isoformat(),
            data_final=data_final.isoformat(),
        ).info("storage.cursor.updated")


__all__ = ["CursorRepository"]
