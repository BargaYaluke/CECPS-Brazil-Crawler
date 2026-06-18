# -*- coding: utf-8 -*-
"""Step2: 清洗招标池(report.xlsx 招标主表)+ JOIN 投标链接(DB)。"""
import sys, io, json, sqlite3
import pandas as pd
import numpy as np
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import ROOT, OUT, norm_industry, classify_obj, TODAY

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPORT = ROOT / "data/exports/report.xlsx"
DB = ROOT / "data/procurement.db"

t = pd.read_excel(REPORT, sheet_name="招标主表")
print("原始招标:", len(t))

# JOIN 投标链接
con = sqlite3.connect(str(DB))
links = pd.read_sql_query(
    "select pncp_id as PNCP编号, link_sistema_origem as 投标链接, srp as _srp_db, "
    "is_long_term_opportunity as _lt_db from contratacoes", con)
con.close()
t = t.merge(links, on="PNCP编号", how="left")

# ── 清洗 ──
# 金额:CNY;笔误/超大(>5亿)剔除(无企业可承接且多为数据坑);0=预算保密(sigilo)保留并标注
t["金额CNY"] = pd.to_numeric(t["预估金额(CNY)"], errors="coerce")
t["金额万CNY"] = (t["金额CNY"]/1e4).round(1)
t["预算保密"] = t["金额CNY"].fillna(-1) == 0

# 时效
t["剩余天数"] = pd.to_numeric(t["剩余天数"], errors="coerce")

# 物/服/工程 分类(基于葡语原文)
t["标的类型"] = t["标的(原文)"].map(classify_obj)

# 行业(主维度规范化对齐企业六大行业)
t["行业"] = t["主维度"].map(norm_industry)

# ── 可投标池过滤 ──
n0 = len(t)
expired = t["时效状态"].eq("已过期") | (t["剩余天数"] < 0)
huge = t["金额CNY"] > 5e8
works = t["标的类型"].eq("works")

keep = ~expired & ~huge & ~works
audit = {
    "原始": int(n0),
    "剔除_已过期": int(expired.sum()),
    "剔除_金额>5亿(笔误/超限)": int(huge.sum()),
    "剔除_本地工程obra": int((works & ~expired & ~huge).sum()),
    "保留可投标池": int(keep.sum()),
}
clean = t[keep].copy()

# 整理输出列
COLS = {
    "PNCP编号":"pncp_id", "标的(中文梗概)":"obj_zh", "标的(原文)":"obj_pt",
    "采购方式":"modalidade", "行业":"dim", "主维度":"dim_raw", "次维度":"dim2",
    "维度置信度":"dim_conf", "金额CNY":"valor_cny", "金额万CNY":"valor_wan",
    "预算保密":"sigilo", "采购机构":"orgao", "级别":"esfera", "州":"uf",
    "城市":"municipio", "大区":"regiao", "投标截止":"deadline", "剩余天数":"dias",
    "时效状态":"prazo_status", "电子拍卖":"e_auction", "价格登记":"srp",
    "长期机会":"long_term", "标的类型":"obj_type", "投标链接":"link",
}
clean = clean.rename(columns=COLS)[list(COLS.values())].copy()
clean["deadline"] = clean["deadline"].astype(str).str[:10]
clean["srp_bool"] = clean["srp"].eq("是")
clean["lt_bool"] = clean["long_term"].eq("是")

print("\n过滤审计:")
for k,v in audit.items():
    print(f"  {k}: {v}")
print("\n可投标池 行业分布:")
print(clean["dim"].value_counts(dropna=False).to_string())
print("\n可投标池 标的类型:")
print(clean["obj_type"].value_counts(dropna=False).to_string())
print("\n时效(剩余天数)分桶: 0-7 / 8-30 / 31-90 / >90 / 无截止(NaN):")
d = clean["dias"]
print(" ", int(((d>=0)&(d<=7)).sum()), int(((d>7)&(d<=30)).sum()),
      int(((d>30)&(d<=90)).sum()), int((d>90).sum()), int(d.isna().sum()))
print("\nSRP(价格登记)=是:", int(clean["srp_bool"].sum()), " 长期机会=是:", int(clean["lt_bool"].sum()),
      " 有投标链接:", int(clean["link"].notna().sum()))

clean.to_csv(OUT/"tenders_clean.csv", index=False, encoding="utf-8-sig")
recs = clean.where(pd.notna(clean), None).to_dict(orient="records")
(OUT/"tenders_clean.json").write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
(OUT/"tender_filter_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")
print("\n已保存 tenders_clean.csv/.json (", len(clean), "行)")
