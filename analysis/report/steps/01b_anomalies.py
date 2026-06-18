# -*- coding: utf-8 -*-
"""钻取具体异常记录,判断是单位错/解析错/一次性垃圾,以定清洗规则。"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
pd.set_option("display.width", 240); pd.set_option("display.max_columns", 40)
pd.set_option("display.max_colwidth", 45)
SRC = Path("data/exports/report.xlsx")

print("="*30, "招标主表:金额 top10", "="*30)
m = pd.read_excel(SRC, sheet_name="招标主表")
cols = ["PNCP编号","采购方式","预估金额(BRL)","标的(原文)","采购机构","州"]
print(m.nlargest(10, "预估金额(BRL)")[cols].to_string())
print("\n金额=0 的占比与采购方式分布:")
print(m[m["预估金额(BRL)"]==0]["采购方式"].value_counts().head())
print("\n金额 BRL 分桶:")
import numpy as np
bins=[-1,0,1e4,1e5,1e6,1e7,1e8,1e9,1e12]
labels=["=0","(0,1万]","(1万,10万]","(10万,100万]","(100万,1000万]","(1000万,1亿]","(1亿,10亿]",">10亿"]
print(pd.cut(m["预估金额(BRL)"],bins=bins,labels=labels).value_counts().reindex(labels))

print("\n"+"="*30, "已签合同:金额 top10", "="*30)
c = pd.read_excel(SRC, sheet_name="已签合同")
cc=["合同编号","中标供应商","合同金额(BRL)","采购机构","州","签订日","到期日"]
print(c.nlargest(10,"合同金额(BRL)")[cc].to_string())
print("\n合同金额分桶:")
print(pd.cut(c["合同金额(BRL)"],bins=bins,labels=labels).value_counts().reindex(labels))

print("\n"+"="*30, "采购明细:项号 & 单价异常", "="*30)
it = pd.read_excel(SRC, sheet_name="采购明细")
print("项号 > 1000 的行(应为小序号):", (it["项号"]>1000).sum())
print(it[it["项号"]>1000][["PNCP编号","项号","明细描述","单价(BRL)","小计(BRL)"]].head(8).to_string())
print("\n单价 top5:")
print(it.nlargest(5,"单价(BRL)")[["PNCP编号","明细描述","数量","单价(BRL)","小计(BRL)"]].to_string())

print("\n"+"="*30, "年度采购计划:重复钻取", "="*30)
p = pd.read_excel(SRC, sheet_name="年度采购计划")
full_dup = p.duplicated(keep=False).sum()
print(f"整行完全重复行数: {full_dup} / {len(p)}")
key=["PCA编号","采购标的","期望采购日","预估金额(BRL)"]
print(f"4键(含金额)重复行数: {p.duplicated(subset=key,keep=False).sum()}")
# 看一个重复样例
d = p[p.duplicated(subset=['PCA编号','采购标的','期望采购日'],keep=False)].sort_values(['PCA编号','采购标的'])
print("\n重复样例(同 PCA编号+标的+日期):")
print(d.head(6)[["PCA编号","采购标的","预估金额(BRL)","期望采购日","上级分类"]].to_string())

print("\n"+"="*30, "机构画像:累计额 top5", "="*30)
o = pd.read_excel(SRC, sheet_name="机构画像")
print(o.nlargest(5,"近2年累计额(BRL)")[["机构名称","州","近2年招标数","近2年累计额(BRL)","机构分层"]].to_string())
