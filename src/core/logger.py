"""项目统一日志器。

基于 ``loguru``,输出 JSON 结构化日志到 stderr。
使用方式::

    from src.core.logger import logger
    logger.bind(url=url).info("http.request")

import 时会自动按 ``LOG_LEVEL`` 环境变量初始化;
调用方需要时可通过 :func:`configure` 重新配置 sink。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from loguru import logger as _logger

_INITIALIZED = False


def _json_sink(message: Any) -> None:
    """把 loguru record 序列化为单行 JSON 写到 stderr。"""
    record = message.record
    payload: dict[str, Any] = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "msg": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
    }
    # extra 字段(通过 logger.bind(...) 注入)
    if record["extra"]:
        payload["extra"] = record["extra"]
    if record["exception"] is not None:
        exc = record["exception"]
        payload["exception"] = {
            "type": exc.type.__name__ if exc.type else None,
            "value": str(exc.value) if exc.value else None,
        }
    sys.stderr.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def configure(level: str | None = None, json_output: bool = True) -> None:
    """重置日志 sink。

    Args:
        level: 日志级别(DEBUG / INFO / WARNING / ERROR);``None`` 时读 ``LOG_LEVEL`` 环境变量,缺省 INFO。
        json_output: True 用 JSON 单行格式;False 用 loguru 默认彩色文本(便于本地调试)。
    """
    global _INITIALIZED

    resolved_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    _logger.remove()
    if json_output:
        _logger.add(_json_sink, level=resolved_level, backtrace=True, diagnose=False)
    else:
        _logger.add(
            sys.stderr,
            level=resolved_level,
            backtrace=True,
            diagnose=False,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{module}:{function}:{line}</cyan> - <level>{message}</level>"
            ),
        )
    _INITIALIZED = True


# 首次 import 时自动初始化(JSON 模式)。
if not _INITIALIZED:
    configure()


logger = _logger
"""项目统一日志器实例,默认输出 JSON 结构化日志。"""


def log_stage(stage: str, status: str, metrics: str = "", seconds: float | None = None) -> None:
    """把一行「阶段指标」追加到 ``logs/pipeline_metrics.csv``。

    周更流水线每个阶段(采集 / 富化 / 翻译 / 导出 / 设备表)跑完调一次,记录
    跑了哪个阶段、状态、关键指标、耗时,便于事后追溯。同时也打一条 INFO 日志。

    列:``时间, 阶段, 状态, 关键指标, 耗时(s)``。CLI 串行调用,无需加锁。
    """
    import csv
    from datetime import datetime
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    csv_path = log_dir / "pipeline_metrics.csv"
    is_new = not csv_path.exists()
    # 新建时用 utf-8-sig(带 BOM,Excel 打开中文不乱码);追加时用纯 utf-8 避免重复 BOM。
    enc = "utf-8-sig" if is_new else "utf-8"
    with csv_path.open("a", encoding=enc, newline="") as fh:
        w = csv.writer(fh)
        if is_new:
            w.writerow(["时间", "阶段", "状态", "关键指标", "耗时(s)"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            stage, status, metrics,
            "" if seconds is None else f"{seconds:.1f}",
        ])
    logger.bind(stage=stage, status=status, metrics=metrics,
                seconds=round(seconds, 1) if seconds is not None else None).info("pipeline.stage")


__all__ = ["logger", "configure", "log_stage"]
