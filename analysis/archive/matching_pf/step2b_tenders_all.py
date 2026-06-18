# -*- coding: utf-8 -*-
"""Step2b(纯契合版): 全量招标池 —— 不剔除已过期/工程标(用户要求不考虑时效与本地化可行性),
   仅剔除金额>5亿的数据坑。输出 tenders_all.json。"""
import sys, io, json, sqlite3
import pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import ROOT, OUT, norm_industry, classify_obj

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPORT = ROOT / "data/exports/report.xlsx"
DB = ROOT / "data/procurement.db"

t = pd.read_excel(REPORT, sheet_name="招标主表")
print("原始招标:", len(t))

con = sqlite3.connect(str(DB))
links = pd.read_sql_query(
    "select pncp_id as PNCP编号, link_sistema_origem as 投标链接, srp as _srp_db, "
    "is_long_term_opportunity as _lt_db from contratacoes", con)
con.close()
t = t.merge(links, on="PNCP编号", how="left")

t["金额CNY"] = pd.to_numeric(t["预估金额(CNY)"], errors="coerce")
t["金额万CNY"] = (t["金额CNY"]/1e4).round(1)
t["预算保密"] = t["金额CNY"].fillna(-1) == 0
t["剩余天数"] = pd.to_numeric(t["剩余天数"], errors="coerce")
t["标的类型"] = t["标的(原文)"].map(classify_obj)
t["行业"] = t["主维度"].map(norm_industry)

huge = t["金额CNY"] > 5e8
clean = t[~huge].copy()
print(f"剔除金额>5亿(数据坑): {int(huge.sum())} | 保留全量池: {len(clean)} (含已过期/工程标)")

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

print("\n时效状态分布:")
print(clean["prazo_status"].value_counts(dropna=False).to_string())
print("\n标的类型分布:")
print(clean["obj_type"].value_counts(dropna=False).to_string())

recs = clean.where(pd.notna(clean), None).to_dict(orient="records")
(OUT/"tenders_all.json").write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
print("\n已保存 tenders_all.json (", len(clean), "行)")
