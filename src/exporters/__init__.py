"""导出层(exporters)。

把 storage 中的结构化数据导出为业务可用的报告。

模块计划:
    excel.py       openpyxl 多 Sheet 报告(主表 / 明细 / 机构 / 透视 / 规则)
    parquet.py     大数据量场景下的 Parquet 列存导出

Excel Sheet 结构(已实现,见 excel.py):
    Sheet 1  招标主表(contratacoes + 六大维度中文标签)
    Sheet 2  采购明细(itens)
    Sheet 3  已签合同(contratos,竞品)
    Sheet 4  年度采购计划(pca_itens,商机前置)
    Sheet 5  维度透视
    Sheet 6  过滤审计(filtered_log)
"""

from .excel import export_to_excel

__all__ = ["export_to_excel"]
