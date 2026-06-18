"""被过滤记录的审计落地(Parquet 分区)。

约定:被过滤的记录不能丢,要写 ``data/filtered_log/``,
且**只追加,不覆盖**。

分区路径::

    data/filtered_log/year=2026/month=05/day=27/part-0.parquet

每次写入一个新 ``part-{N}.parquet``,N 是当天已有 part 数。这样
重跑也不会冲突,简单 + 容错。

Parquet 依赖 ``pyarrow``;首次导入时 lazy import,模块加载不会失败。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..core.logger import logger


def _partition_dir(root: Path, when: date) -> Path:
    return (
        root / f"year={when.year}" / f"month={when.month:02d}" / f"day={when.day:02d}"
    )


def _next_part_path(partition: Path) -> Path:
    """在分区目录里找下一个空闲的 part-N.parquet。"""
    existing = list(partition.glob("part-*.parquet"))
    return partition / f"part-{len(existing)}.parquet"


def _normalize_for_parquet(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 dict / list / datetime 字段转成 Parquet 友好类型。

    Parquet 不接受任意嵌套字典 — 把 ``raw_json`` 这类列序列化为 JSON 字符串,
    其它字段保持原样。
    """
    normalized: list[dict[str, Any]] = []
    for r in records:
        item: dict[str, Any] = {}
        for k, v in r.items():
            if isinstance(v, (dict, list)):
                item[k] = json.dumps(v, ensure_ascii=False, default=str)
            elif isinstance(v, datetime):
                item[k] = v.isoformat()
            elif isinstance(v, date):
                item[k] = v.isoformat()
            else:
                item[k] = v
        normalized.append(item)
    return normalized


def write_filtered_log(
    records: list[dict[str, Any]],
    root: Path,
    when: date | datetime | None = None,
) -> Path | None:
    """追加写一批被过滤记录到当日分区。

    Args:
        records: dict 列表。每条至少应该有 ``pncp_id`` / ``filter_rule_id``
            / ``filter_reason`` / ``crawl_timestamp`` 等字段。
        root: filtered_log 根目录(通常 ``data/filtered_log``)。
        when: 决定写到哪个分区的日期;``None`` 时用 ``datetime.utcnow().date()``。

    Returns:
        实际写入的文件路径;``records`` 为空时返回 ``None``,不创建目录。

    Raises:
        ImportError: pyarrow 未安装(``pip install pyarrow``)。
    """
    if not records:
        return None

    if isinstance(when, datetime):
        when = when.date()
    elif when is None:
        when = datetime.utcnow().date()

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError(
            "pyarrow is required to write filtered_log Parquet. "
            "Run: pip install pyarrow"
        ) from exc

    partition = _partition_dir(root, when)
    partition.mkdir(parents=True, exist_ok=True)
    target = _next_part_path(partition)

    cleaned = _normalize_for_parquet(records)
    table = pa.Table.from_pylist(cleaned)
    pq.write_table(table, target)

    logger.bind(
        path=str(target),
        n=len(records),
        partition=str(partition),
    ).info("filter.filtered_log.written")
    return target


__all__ = ["write_filtered_log"]
