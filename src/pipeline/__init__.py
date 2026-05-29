"""Pipeline 编排层。

站在所有业务层(fetchers / storage / filters / ...)之上做编排,
**只调下层,不被下层调**。跟 :mod:`src.cli` 类似,但更易测试、可被
多个 CLI 子命令复用。
"""

from .orchestrator import run_publicacao_with_itens

__all__ = ["run_publicacao_with_itens"]
