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

__all__ = ["logger", "configure"]
