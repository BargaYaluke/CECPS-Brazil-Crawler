"""项目自定义异常层级。

根据 CLAUDE.md 第 6 节: 所有 except 必须捕获这里的类型,禁止裸 ``except:``。
按层分类,便于上层做差异化处理(重试 / 跳过 / 报警)。
"""
from __future__ import annotations


class CrawlerError(Exception):
    """所有爬虫相关异常的根类。"""


# ─── 配置层 ──────────────────────────────────────────────────────────────
class ConfigError(CrawlerError):
    """配置文件不存在、解析失败或字段缺失。"""


# ─── 采集层(fetchers) ────────────────────────────────────────────────
class FetcherError(CrawlerError):
    """fetcher 拉数据失败的根异常。"""


class HttpError(FetcherError):
    """HTTP 请求层错误(超时、5xx、连接失败等)。"""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class RateLimitExceededError(HttpError):
    """命中目标站点限流(HTTP 429)。"""


class F5ChallengeError(HttpError):
    """httpx 直连撞上 F5 BIG-IP ASM JS 挑战页(且无可用 cookie 回调)。

    F5 的 ``TS*`` cookies 绑 TLS 会话指纹,httpx 重放 cookie 无效 —— 唯一解是
    全程真浏览器。:class:`src.fetchers.pncp_session.PncpSession` 的 ``auto`` 模式
    捕获本异常后会自动 fallback 到 ``BrowserSession``。
    """


class BrowserError(FetcherError):
    """真浏览器(Playwright)启动 / 导航 / cookie 抽取失败。

    用于解 F5 ASM / Cloudflare 等 JS 挑战的场景。
    """


# ─── 富化层(enrichers) ──────────────────────────────────────────────
class EnrichmentError(CrawlerError):
    """enricher 调用外部数据源失败(BrasilAPI / IBGE / BCB 等)。"""


# ─── 过滤层(filters) ────────────────────────────────────────────────
class FilterError(CrawlerError):
    """filter 引擎执行规则失败(规则格式错、字段缺失等)。"""


# ─── 分类层(classifiers) ────────────────────────────────────────────
class ClassifierError(CrawlerError):
    """六大维度分类失败。"""


# ─── 存储层(storage) ────────────────────────────────────────────────
class StorageError(CrawlerError):
    """数据库读写或 schema 不一致。"""


# ─── 导出层(exporters) ──────────────────────────────────────────────
class ExportError(CrawlerError):
    """Excel / Parquet 导出失败。"""


__all__ = [
    "CrawlerError",
    "ConfigError",
    "FetcherError",
    "HttpError",
    "RateLimitExceededError",
    "F5ChallengeError",
    "BrowserError",
    "EnrichmentError",
    "FilterError",
    "ClassifierError",
    "StorageError",
    "ExportError",
]
