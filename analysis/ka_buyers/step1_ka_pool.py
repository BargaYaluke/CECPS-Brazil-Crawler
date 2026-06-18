# -*- coding: utf-8 -*-
"""Step1: 金主(KA买家)池 + 未过期标 + 本地富集。

口径(用户 2026-06-17 确认):
  * 金主 = 综合分 排名 top-200 采购机构:r_freq*0.45 + r_val(总额)*0.35 + r_med(单标中位)*0.20
  * 取这些买家的 *未过期* 标(时效状态≠已过期 且 剩余天数≥0,或无截止)
  * 全部富集本地可得:投标链接(DB link_sistema_origem)/ PNCP官方门户链接(构造)/
    ME-EPP 限制(itens 表 tipo_beneficio 聚合)/ 长期挂网 / 价格登记SRP / 真实提交截止日期
无需任何 PNCP 在线 API(ME-EPP/链接均本地齐备)。
产物: outputs/ka_bids.json(标级) + outputs/ka_buyers.csv(200家金主画像)
"""
import sys, io, json, sqlite3, math
import pandas as pd
import numpy as np
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "outputs"
OUT.mkdir(exist_ok=True)
REPORT = ROOT / "data/exports/report.xlsx"
DB = ROOT / "data/procurement.db"

# ─── 载入招标主表 ───────────────────────────────────────────────
t = pd.read_excel(REPORT, sheet_name="招标主表")
t["金额CNY"] = pd.to_numeric(t["预估金额(CNY)"], errors="coerce")
t["剩余天数"] = pd.to_numeric(t["剩余天数"], errors="coerce")

# ─── DB 富集:链接 + ME/EPP(itens) + 长期 + srp + cnpj ──────────
con = sqlite3.connect(str(DB))
links = pd.read_sql_query(
    "select pncp_id as PNCP编号, link_sistema_origem as 投标链接, orgao_cnpj, "
    "is_long_term_opportunity as _lt, srp as _srp, modalidade_nome as _modal "
    "from contratacoes", con)
items = pd.read_sql_query("select pncp_id, tipo_beneficio_nome from itens", con)
con.close()

def meepp_label(names):
    names = {str(n) for n in names if n and str(n) not in ("None", "nan")}
    excl = [n for n in names if "xclusiv" in n.lower()]
    cota = [n for n in names if "ota" in n.lower() and "reserv" in n.lower()]
    sub  = [n for n in names if "ubcontrat" in n.lower()]
    if excl and len(excl) == len(names):
        return "全部专属"
    if excl:
        return "部分专属"
    if cota:
        return "预留份额"
    if sub:
        return "要求分包"
    return "无"

meepp = (items.groupby("pncp_id")["tipo_beneficio_nome"]
         .apply(lambda s: meepp_label(set(s.dropna()))).rename("_meepp").reset_index()
         .rename(columns={"pncp_id": "PNCP编号"}))

t = t.merge(links, on="PNCP编号", how="left").merge(meepp, on="PNCP编号", how="left")
t["_meepp"] = t["_meepp"].fillna("无")

# ─── 金主综合分(在 *全量可投池* 上算,剔>5亿数据坑) ──────────
pool = t[~(t["金额CNY"] > 5e8)].copy()
g = pool.groupby("采购机构").agg(
    n_bids=("PNCP编号", "count"),
    total_cny=("金额CNY", "sum"),
    median_cny=("金额CNY", "median"),
    max_cny=("金额CNY", "max"),
    esfera=("级别", lambda s: s.mode().iat[0] if len(s.mode()) else None),
    uf=("州", lambda s: s.mode().iat[0] if len(s.mode()) else None),
    gdp=("GDP分层", lambda s: s.mode().iat[0] if len(s.mode()) else None),
).reset_index()
g["r_freq"] = g["n_bids"].rank(pct=True)
g["r_val"]  = g["total_cny"].rank(pct=True)
g["r_med"]  = g["median_cny"].rank(pct=True)
g["ka_score"] = g["r_freq"]*0.45 + g["r_val"]*0.35 + g["r_med"]*0.20
g = g.sort_values("ka_score", ascending=False).reset_index(drop=True)
top = g.head(200).copy()
top["ka_rank"] = np.arange(1, len(top)+1)
top["total_wan"] = (top["total_cny"]/1e4).round(1)
top["median_wan"] = (top["median_cny"]/1e4).round(1)
top["max_wan"] = (top["max_cny"]/1e4).round(1)
ESF = {"M": "市政", "E": "州", "F": "联邦", "N": "国企/全国", "D": "特区"}
top["级别"] = top["esfera"].map(ESF).fillna(top["esfera"])
ka_names = set(top["采购机构"])
print(f"金主 top-200: 覆盖买家 {len(g)} 家中选 200;级别分布 {top['esfera'].value_counts().to_dict()}")

# ─── 取金主名下 *未过期* 标 ──────────────────────────────────────
expired = pool["时效状态"].eq("已过期") | (pool["剩余天数"] < 0)
ka = pool[pool["采购机构"].isin(ka_names) & ~expired].copy()
print(f"金主名下未过期标: {len(ka)} 条")

# ─── PNCP 官方门户链接 构造 ──────────────────────────────────────
def portal_link(pncp):
    s = str(pncp or "")
    try:
        left, ano = s.split("/")
        parts = left.split("-")
        cnpj, seq = parts[0], parts[-1]
        return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{int(seq)}"
    except Exception:
        return None

def fix_scheme(u):
    if u is None:
        return None
    u = str(u).strip()
    if not u or u.lower() == "nan":
        return None
    if u.lower().startswith(("http://", "https://")):
        return u
    if "." in u.split("/")[0]:
        return "https://" + u
    return None

# ─── 提交截止日期 显示(复用 step9 口径) ─────────────────────────
def fmt_deadline(dt):
    if pd.isna(dt):
        return None
    dt = pd.to_datetime(dt)
    if (dt.hour, dt.minute) in [(0, 0), (23, 59)]:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M")

def deadline_cell(row):
    f = fmt_deadline(row["投标截止"])
    if f:
        return f
    d = row["剩余天数"]
    if pd.isna(d):
        return "无截止(长期挂网/价格登记)"
    return f"{int(d)}天(采集日2026-05-29起)"

MEEPP_DISPLAY = {
    "全部专属": "仅限ME/EPP(全部标项)·外资子公司通常不符资格,建议核实或改JV",
    "部分专属": "部分标项仅限ME/EPP·大企业可投其余标项",
    "预留份额": "含ME/EPP预留份额·大企业可投主份额",
    "要求分包": "要求向ME/EPP分包",
    "无": "无",
}

bids = []
for _, r in ka.iterrows():
    pncp = r["PNCP编号"]
    is_lt = (r.get("_lt") == 1) or (str(r.get("_modal")) == "Credenciamento") or (r.get("长期机会") == "是")
    bids.append({
        "pncp": pncp,
        "ka_buyer": r["采购机构"],
        "ka_rank": int(top.loc[top["采购机构"] == r["采购机构"], "ka_rank"].iat[0]),
        "obj_zh": r["标的(中文梗概)"],
        "obj_pt": r["标的(原文)"],
        "valor_wan": None if pd.isna(r["金额CNY"]) else round(r["金额CNY"]/1e4, 1),
        "sigilo": bool(r["金额CNY"] == 0) if pd.notna(r["金额CNY"]) else False,
        "deadline": deadline_cell(r),
        "dias": None if pd.isna(r["剩余天数"]) else int(r["剩余天数"]),
        "uf": r["州"],
        "municipio": r.get("城市"),
        "esfera": ESF.get(r["级别"], r["级别"]),
        "orgao": r["采购机构"],
        "modalidade": r["采购方式"],
        "srp": "是" if r.get("价格登记") == "是" else "否",
        "long_term": "是" if is_lt else "否",
        "link": fix_scheme(r.get("投标链接")),
        "portal": portal_link(pncp),
        "meepp": MEEPP_DISPLAY.get(r["_meepp"], "无"),
        "meepp_raw": r["_meepp"],
        "dim_raw": r.get("主维度"),
        "dim2_raw": r.get("次维度"),
    })

(OUT/"ka_bids.json").write_text(json.dumps(bids, ensure_ascii=False, indent=1), encoding="utf-8")

# 金主清单
buyers_cols = ["ka_rank", "采购机构", "级别", "uf", "gdp", "n_bids", "total_wan", "median_wan", "max_wan", "ka_score"]
top_out = top.rename(columns={"uf": "uf", "gdp": "gdp"})[buyers_cols].copy()
top_out["ka_score"] = top_out["ka_score"].round(4)
top_out.to_csv(OUT/"ka_buyers.csv", index=False, encoding="utf-8-sig")

print(f"\n已写 outputs/ka_bids.json ({len(bids)} 标) + ka_buyers.csv (200 金主)")
print("长期挂网=是:", sum(1 for b in bids if b["long_term"] == "是"),
      "| 价格登记=是:", sum(1 for b in bids if b["srp"] == "是"))
print("ME/EPP 分布:", pd.Series([b["meepp_raw"] for b in bids]).value_counts().to_dict())
print("有投标链接:", sum(1 for b in bids if b["link"]), "| 有官方链接:", sum(1 for b in bids if b["portal"]))
print("金额万CNY 中位:", round(np.median([b["valor_wan"] for b in bids if b["valor_wan"]]), 1))
print("剩余天数分桶 ≤7/8-30/31-90/>90/无:",
      sum(1 for b in bids if b["dias"] is not None and 0 <= b["dias"] <= 7),
      sum(1 for b in bids if b["dias"] is not None and 7 < b["dias"] <= 30),
      sum(1 for b in bids if b["dias"] is not None and 30 < b["dias"] <= 90),
      sum(1 for b in bids if b["dias"] is not None and b["dias"] > 90),
      sum(1 for b in bids if b["dias"] is None))
