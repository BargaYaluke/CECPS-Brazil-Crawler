# -*- coding: utf-8 -*-
"""Deep profile of company master + tender pool to design the matching pipeline."""
import sys, io
import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
pd.set_option("display.max_columns", None); pd.set_option("display.width", 240)

MAIN  = "data/exports/中葡企业机构数据库_企业登记表.xlsx"
ORDER = "data/exports/中葡企业机构数据库_企业登记表_六大行业顺序.xlsx"
REPORT= "data/exports/report.xlsx"

cm = pd.read_excel(MAIN, sheet_name="企业登记表")
co = pd.read_excel(ORDER, sheet_name="企业登记表")

print("########## COMPANY TABLES ##########")
print(f"main rows={len(cm)}  order rows={len(co)}")
for name, df in [("main", cm), ("order", co)]:
    print(f"\n----- {name}: 六大行业 value_counts -----")
    print(df["六大行业"].value_counts(dropna=False).to_string())
    print(f"----- {name}: 公司重要程度 value_counts -----")
    print(df["公司重要程度"].value_counts(dropna=False).to_string())
    print(f"----- {name}: 境内/境外 -----")
    print(df["境内/境外"].value_counts(dropna=False).to_string())
    print(f"----- {name}: 所在国别 (top) -----")
    print(df["所在国别"].value_counts(dropna=False).head(12).to_string())

# field completeness compare per file
print("\n----- per-field non-null counts (main vs order) -----")
for c in cm.columns:
    a = cm[c].notna().sum() if c in cm else -1
    b = co[c].notna().sum() if c in co else -1
    print(f"  {c:18s} main={a:4d}  order={b:4d}")

# do the two files cover same companies? key by 公司全称
sm = set(cm["公司全称"].dropna().astype(str).str.strip())
so = set(co["公司全称"].dropna().astype(str).str.strip())
print(f"\n公司全称: main={len(sm)} order={len(so)} 交集={len(sm&so)} 仅main={len(sm-so)} 仅order={len(so-sm)}")
print("仅在 main 的(样例):", list(sm-so)[:8])
print("仅在 order 的(样例):", list(so-sm)[:8])

# duplicated full names?
print("\nmain 公司全称重复:", cm["公司全称"].duplicated().sum(), "  order:", co["公司全称"].duplicated().sum())

# importance present after merge: for companies, where is importance non-null?
m = cm[["公司全称","公司重要程度","主营业务"]].rename(columns={"公司重要程度":"imp_m","主营业务":"biz_m"})
o = co[["公司全称","公司重要程度","主营业务"]].rename(columns={"公司重要程度":"imp_o","主营业务":"biz_o"})
mg = m.merge(o, on="公司全称", how="outer")
mg["imp"] = mg["imp_m"].fillna(mg["imp_o"])
print("\n合并后 importance 分布:")
print(mg["imp"].value_counts(dropna=False).to_string())
print("biz 主营业务 缺失(两个文件都没有):", ((mg["biz_m"].isna())&(mg["biz_o"].isna())).sum())

print("\n\n########## TENDER POOL (招标主表) ##########")
t = pd.read_excel(REPORT, sheet_name="招标主表")
print("rows:", len(t))
print("\n主维度 value_counts:")
print(t["主维度"].value_counts(dropna=False).to_string())
print("\n采购方式 value_counts (top15):")
print(t["采购方式"].value_counts(dropna=False).head(15).to_string())
print("\n时效状态 value_counts:")
print(t["时效状态"].value_counts(dropna=False).to_string())
print("\n级别 value_counts:")
print(t["级别"].value_counts(dropna=False).to_string())
print("\n价格登记/长期机会/电子拍卖:")
for c in ["价格登记","长期机会","电子拍卖","竞争性招标"]:
    print(f"  {c}: {dict(t[c].value_counts(dropna=False))}")
print("\n预估金额(CNY) 描述 (剔除NaN):")
print(t["预估金额(CNY)"].describe().to_string())
print("金额=0 计数:", (t["预估金额(CNY)"]==0).sum(), " 金额>5亿计数:", (t["预估金额(CNY)"]>5e8).sum())
print("\n剩余天数 描述:")
print(t["剩余天数"].describe().to_string())
print("剩余天数<0:", (t["剩余天数"]<0).sum(), " 0-7:", ((t["剩余天数"]>=0)&(t["剩余天数"]<=7)).sum(),
      " 8-30:", ((t["剩余天数"]>7)&(t["剩余天数"]<=30)).sum(), " >30:", (t["剩余天数"]>30).sum())

# actionable filter rough: 还能投/无截止/临近 & 维度 in industries & dedup
print("\n时效=还能投 或 无截止 的条数:", t["时效状态"].isin(["还能投","无截止","临近截止"]).sum())

# look for a link column anywhere
print("\n招标主表所有列:", list(t.columns))
