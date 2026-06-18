# -*- coding: utf-8 -*-
"""Step6: 核验 1519 条 KA 标的金额准确性。
交叉比对 4 个来源:
  A. report.xlsx 预估金额(BRL) / 预估金额(CNY)
  B. DB contratacoes.valor_total_estimado(BRL) / valor_cny_estimado(CNY)
  C. DB itens 该标全部 valor_total 之和(逐项预算汇总)
检查: ① FX 汇率是否一致(CNY/BRL);② report BRL 是否=DB BRL;③ report BRL 是否≈items汇总
(差异大=可能某字段笔误)。输出 amount_audit.json + 控制台清单。
"""
import sys, io, json, sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
pd.set_option("display.width", 240); pd.set_option("display.max_colwidth", 60)
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "outputs"

bids = json.loads((OUT/"ka_bids.json").read_text("utf-8"))
ka_ids = {b["pncp"] for b in bids}
zh = {b["pncp"]: b["obj_zh"] for b in bids}

rep = pd.read_excel(ROOT/"data/exports/report.xlsx", sheet_name="招标主表")
rep = rep[rep["PNCP编号"].isin(ka_ids)][["PNCP编号", "预估金额(BRL)", "预估金额(CNY)", "采购机构"]].copy()
rep.columns = ["pncp", "r_brl", "r_cny", "orgao"]

con = sqlite3.connect(str(ROOT/"data/procurement.db"))
db = pd.read_sql_query(
    "select pncp_id as pncp, valor_total_estimado as db_brl, valor_cny_estimado as db_cny from contratacoes", con)
it = pd.read_sql_query(
    "select pncp_id as pncp, sum(valor_total) as items_sum, count(*) as n_items, "
    "max(orcamento_sigiloso) as sigilo_item from itens group by pncp_id", con)
con.close()

m = rep.merge(db, on="pncp", how="left").merge(it, on="pncp", how="left")

# ── FX 汇率 ──
m["rate_row"] = m["r_cny"] / m["r_brl"]
valid = m[(m["r_brl"] > 0) & (m["r_cny"] > 0)]
rate = float(np.median(valid["rate_row"]))
print(f"FX 汇率(CNY/BRL) 中位 = {rate:.5f};样本 {len(valid)};"
      f"min/max = {valid['rate_row'].min():.5f}/{valid['rate_row'].max():.5f}")

def pct_diff(a, b):
    a = pd.to_numeric(a, errors="coerce"); b = pd.to_numeric(b, errors="coerce")
    return (a - b).abs() / b.replace(0, np.nan)

# 检查项
m["fx_off"] = ((m["rate_row"] - rate).abs() / rate > 0.02) & (m["r_brl"] > 0)
m["brl_vs_db"] = pct_diff(m["r_brl"], m["db_brl"])
m["brl_vs_items"] = pct_diff(m["r_brl"], m["items_sum"])
m["flag_db"] = (m["brl_vs_db"] > 0.01) & m["db_brl"].notna() & (m["db_brl"] > 0)
m["flag_items"] = (m["brl_vs_items"] > 0.05) & m["items_sum"].notna() & (m["items_sum"] > 0) & (m["r_brl"] > 0)
m["r_wan"] = (m["r_cny"] / 1e4).round(1)
m["items_wan_cny"] = (m["items_sum"] * rate / 1e4).round(1)

print("\n=== 一致性汇总 ===")
print("FX 不一致行:", int(m["fx_off"].sum()))
print("report BRL ≠ DB BRL (>1%):", int(m["flag_db"].sum()))
print("report BRL ≠ items汇总 (>5%, 有items):", int(m["flag_items"].sum()))
print("金额=0(预算保密):", int((m["r_cny"] == 0).sum()))
print("有 items 预算保密标记:", int((m["sigilo_item"] == 1).sum()))

print("\n=== 金额 TOP 20(肉眼核验合理性) ===")
top = m.sort_values("r_cny", ascending=False).head(20)
for _, r in top.iterrows():
    flag = []
    if r["fx_off"]: flag.append("FX异常")
    if r["flag_db"]: flag.append(f"≠DB({r['brl_vs_db']*100:.0f}%)")
    if r["flag_items"]: flag.append(f"≠items({r['brl_vs_items']*100:.0f}%)")
    print(f"  {r['r_wan']:>10.1f}万 | items≈{r['items_wan_cny'] if pd.notna(r['items_wan_cny']) else '—'}万 "
          f"| {'⚠'+','.join(flag) if flag else 'OK'} | {str(zh.get(r['pncp']))[:40]}")

print("\n=== 被标记可疑的标(全部) ===")
susp = m[m["fx_off"] | m["flag_db"] | m["flag_items"]].sort_values("r_cny", ascending=False)
print(f"共 {len(susp)} 条")
for _, r in susp.head(40).iterrows():
    flag = []
    if r["fx_off"]: flag.append("FX异常")
    if r["flag_db"]: flag.append(f"≠DB {r['db_brl']:.0f}BRL")
    if r["flag_items"]: flag.append(f"items={r['items_wan_cny']:.0f}万vs报{r['r_wan']:.0f}万")
    print(f"  {r['pncp']} | {r['r_wan']:.1f}万 | {','.join(flag)} | {str(zh.get(r['pncp']))[:36]}")

# ── 每标 金额核验分类 ──
con2 = sqlite3.connect(str(ROOT/"data/procurement.db"))
srp = {r[0]: r[1] for r in con2.execute("select pncp_id, srp from contratacoes")}
con2.close()

def verify_cat(r):
    rc, rb = r["r_cny"], r["r_brl"]
    if pd.isna(rc) or rc == 0:
        return "预算保密(sigilo)"
    if pd.notna(rb) and rb <= 1:
        return "象征性/未计价"
    if r["flag_items"]:
        return "估算上限·逐项预算仅部分"
    return "逐项预算一致✓"

m["verify_cat"] = m.apply(verify_cat, axis=1)
print("\n=== 金额核验分类 ===")
print(m["verify_cat"].value_counts().to_string())

# 保存审计
audit = m[["pncp", "r_brl", "r_cny", "r_wan", "db_brl", "db_cny", "items_sum", "items_wan_cny",
           "n_items", "sigilo_item", "rate_row", "fx_off", "flag_db", "flag_items", "verify_cat"]].copy()
audit = audit.where(pd.notna(audit), None)
(OUT/"amount_audit.json").write_text(
    json.dumps({"rate": rate, "rows": audit.to_dict(orient="records")}, ensure_ascii=False, default=str),
    encoding="utf-8")
print("\n已写 outputs/amount_audit.json")
