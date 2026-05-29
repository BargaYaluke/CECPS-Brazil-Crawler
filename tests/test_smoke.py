"""脚手架烟雾测试 — 验证关键导入与 CLI 启动。

P0 阶段只检查"骨架可以跑起来",不测业务逻辑。
"""
from __future__ import annotations


def test_package_version() -> None:
    import src

    assert src.__version__


def test_core_imports() -> None:
    from src.core import exceptions, http_client, logger, settings  # noqa: F401
    from src.core.exceptions import CrawlerError, FetcherError, HttpError
    from src.core.http_client import HttpClient

    assert issubclass(HttpError, FetcherError)
    assert issubclass(FetcherError, CrawlerError)
    assert HttpClient is not None


def test_settings_defaults() -> None:
    from src.core.settings import get_http_settings

    cfg = get_http_settings()
    assert cfg["user_agent"] == "BrazilProcurementBot/1.0"
    assert cfg["timeout"] == 30
    assert cfg["max_retries"] == 3


def test_cli_help_exits_zero() -> None:
    from click.testing import CliRunner

    from src.cli import cli

    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "巴西 PNCP 招投标爬虫" in result.output
