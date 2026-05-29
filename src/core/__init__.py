"""核心基础设施层。

提供 logger / exceptions / http_client / settings,被其它所有层依赖。
此层不允许反向 import fetchers / enrichers / filters 等业务模块。
"""
