"""巴西招投标爬虫包根。

包结构(严格分层,模块间禁止跨层调用):
    core/        基础设施(logger / exceptions / http_client / settings)
    fetchers/    采集层(只负责拉数据,不做业务逻辑)
    enrichers/   富化层(CNPJ / 区域 / 汇率)
    filters/     过滤标注层(读 config/filter_rules.yaml)
    classifiers/ 六大维度分类
    storage/     SQLAlchemy 模型与持久化
    exporters/   Excel / Parquet 导出
"""

__version__ = "0.1.0"
