# -*- coding: utf-8 -*-
"""二轮分析:企业↔招投标匹配 / 本地化中标量化 / 可投机会过滤漏斗 / 建议验证候选指标。
输出 analysis/report/outputs/stats2.json。
"""
from __future__ import annotations
import json, re, sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

CL = Path("analysis/report/outputs/cleaned"); OUT = Path("analysis/report/outputs")

def J(o):
    if isinstance(o, dict):  return {str(k): J(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [J(x) for x in o]
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (isinstance(o, float) and (np.isnan(o) or np.isinf(o))) else round(float(o), 4)
    if isinstance(o, pd.Timestamp): return o.strftime("%Y-%m-%d")
    return o

ctr = pd.read_parquet(CL / "招标主表.parquet")
con = pd.read_parquet(CL / "已签合同.parquet")
# 补国别
dbc = sqlite3.connect("data/procurement.db")
pais = pd.read_sql("select pncp_id as 合同编号, codigo_pais_fornecedor as pais from contratos", dbc)
dbc.close()
con = con.merge(pais, on="合同编号", how="left")

R = {}

# ════ 1. 企业↔招投标 匹配(合同.对应招标 == 招标.PNCP编号)════
ctr_key = ctr[["PNCP编号", "主维度", "采购方式", "标的(中文梗概)", "标的(原文)",
               "valor_brl_clean", "采购机构", "州", "时效状态", "投标截止"]].rename(
    columns={"PNCP编号": "对应招标", "valor_brl_clean": "招标预估BRL"})
m = con.merge(ctr_key, on="对应招标", how="inner", suffixes=("_合同", "_招标"))
m = m.drop_duplicates(subset=["合同编号"])
R["match"] = {
    "n_contracts_total": int(len(con)),
    "n_matched": int(len(m)),
    "match_rate_pct": round(100 * len(m) / len(con), 3),
    "note": "匹配键=合同.对应招标(linked_contratacao_pncp_id)==招标.PNCP编号;"
            "匹配率低是因为已签合同多为历史授标、与本周招标快照时间窗不同。匹配上的即可看到「招标标的→中标企业→金额」全链路。",
}
# 匹配明细表(按合同额降序)
mt = m.assign(_cv=m["valor_brl_clean"].where(m["valor_brl_clean"] > 0)).sort_values("_cv", ascending=False)
cols = ["对应招标", "主维度", "采购方式", "标的(中文梗概)", "中标供应商", "ni_fornecedor",
        "pais", "招标预估BRL", "valor_brl_clean", "采购机构_招标", "州_招标"]
cols = [c for c in cols if c in mt.columns]
R["match_records"] = mt[cols].head(40).rename(columns={
    "valor_brl_clean": "合同金额BRL", "采购机构_招标": "采购机构", "州_招标": "州",
    "ni_fornecedor": "供应商CNPJ"}).to_dict("records")
# 匹配集里的维度分布
R["match_by_dimension"] = (mt.groupby("主维度").agg(n=("合同编号", "size"),
    total=("_cv", "sum")).reset_index().sort_values("n", ascending=False).to_dict("records"))

# ════ 2. 本地化中标量化 ════
con["_cv"] = con["valor_brl_clean"].where(con["valor_brl_clean"] > 0)
con["is_foreign_direct"] = con["pais"].notna() & (con["pais"] != "BRA")
# 主体 PJ(法人/可本地化主体) vs PF(自然人)
by_tipo = con.groupby("主体").agg(n=("合同编号", "size"), total=("_cv", "sum"),
    n_sup=("中标供应商", "nunique")).reset_index().to_dict("records")
# 启发式:疑似"外资本地化子公司"(名称含 DO BRASIL 或 全球品牌关键词)且登记为本地(pais=BRA/缺失)
BRANDS = ["DO BRASIL","DO BRAZIL","SIEMENS","HUAWEI","SAMSUNG"," LG ","BOSCH","PHILIPS",
    "GENERAL ELECTRIC","GE HEALTHCARE","HYUNDAI","VOLVO","SCANIA","MERCEDES","CATERPILLAR",
    "JOHN DEERE","MICROSOFT","ORACLE"," IBM ","DELL","HEWLETT","LENOVO","ATLAS COPCO"," ABB ",
    "SCHNEIDER","HONEYWELL"," 3M ","JOHNSON","ROCHE","PFIZER","NOVARTIS","BAYER","MEDTRONIC",
    "GILEAD","BIO-RAD","THERMO","HEALTHINEERS","NOKIA","ERICSSON","CISCO","XEROX","CANON",
    "EPSON","TOYOTA","NISSAN"," FORD"," FIAT","RENAULT","PEUGEOT"," MAN ","IVECO","KOMATSU",
    "HITACHI","MITSUBISHI","PANASONIC","TOSHIBA"," NEC ","FUJITSU"," SAP ","VMWARE"," ZTE",
    "XIAOMI"," BYD ","CRRC","SANY","XCMG","ZOOMLION","DAHUA","HIKVISION"," TCL ","HAIER",
    "GREE","MIDEA","FOTON","CHERY","ABBOTT","SIEMENS","STRYKER","BAXTER","FRESENIUS",
    "GETINGE","DRAGER","DRÄGER","NESTLE","NESTLÉ","UNILEVER","KOREA","NIPPON","DEUTSCHE"]
def is_loc_mnc(name):
    if not isinstance(name, str): return False
    u = " " + name.upper() + " "
    return any(b in u for b in BRANDS)
con["is_loc_mnc"] = con["中标供应商"].apply(is_loc_mnc) & (~con["is_foreign_direct"])
loc = con[con["is_loc_mnc"]]
R["localization"] = {
    "n_unique_suppliers": int(con["中标供应商"].nunique()),
    "by_tipo_pessoa": by_tipo,  # PJ法人 vs PF自然人
    "foreign_direct": {"n_contracts": int(con["is_foreign_direct"].sum()),
        "n_suppliers": int(con.loc[con["is_foreign_direct"], "中标供应商"].nunique()),
        "value_BRL": float(con.loc[con["is_foreign_direct"], "_cv"].sum()),
        "pct_contracts": round(100*con["is_foreign_direct"].mean(), 4)},
    "localized_mnc_heuristic": {
        "n_suppliers": int(loc["中标供应商"].nunique()),
        "n_contracts": int(len(loc)),
        "value_BRL": float(loc["_cv"].sum()),
        "pct_contracts": round(100*len(loc)/len(con), 3),
        "note": "启发式:中标供应商名称含『DO BRASIL』或全球品牌关键词、且非外企直接(已登记为本地法人)。"
                "属名称匹配的下限估计(真实本地子公司更多,因多数子公司用本地化名称),仅作量级佐证。"},
    "examples_localized_mnc": loc.sort_values("_cv", ascending=False)[
        ["中标供应商", "pais", "valor_brl_clean", "采购机构", "州"]].drop_duplicates("中标供应商").head(25).to_dict("records"),
}
# 概率视角:本地法人(PJ)中标占比 vs 外企直接
pj = next((r for r in by_tipo if r["主体"] == "PJ"), {"n": 0, "total": 0})
R["localization"]["probability_view"] = {
    "PJ法人_中标笔数": pj.get("n", 0), "PJ法人_中标额": pj.get("total", 0),
    "PJ法人_笔数占比pct": round(100*pj.get("n", 0)/len(con), 2),
    "外企直接_笔数": int(con["is_foreign_direct"].sum()),
    "对比": "本地法人(PJ)身份几乎承包全部中标;纯外企直接身份中标≈0.04% → 本地化是中标的前提条件。",
}

# ════ 3. 可投机会过滤漏斗 ════
open_mask = ctr["时效状态"].isin(["还能投", "临近截止"])
classified = ctr["主维度"] != "未分类"
disclosed = ctr["valor_brl_clean"].fillna(0) > 0
friendly = ctr["采购方式"].str.contains("Eletrôn", na=False) | ctr["价格登记"].eq("是")
funnel = [
    ("①已采集招标(已过PNCP相关性硬过滤)", int(len(ctr))),
    ("②在投(还能投+临近截止)", int(open_mask.sum())),
    ("③在投 且 命中六大维度(剔除未分类)", int((open_mask & classified).sum())),
    ("④在投+已分类 且 已披露预算", int((open_mask & classified & disclosed).sum())),
    ("⑤在投+已分类+已披露 且 友好方式(电子/SRP)", int((open_mask & classified & disclosed & friendly).sum())),
]
R["funnel"] = {
    "stages": [{"阶段": a, "数量": b} for a, b in funnel],
    "note": "原始8357条已是PNCP相关性硬过滤后的入库结果(本次导出『过滤审计』为空=本次未回灌被过滤明细)。"
            "7687条仅按『时效=在投』筛得,仍含2066条未分类;真正『在投且属六大维度』为③,逐层收窄见漏斗。",
    "open_unclassified": int((open_mask & ~classified).sum()),
}

# ════ 4. 建议验证候选指标(供验证 agent 取数)════
R["rec_metrics"] = {
    "open_now": int(open_mask.sum()),
    "open_classified": int((open_mask & classified).sum()),
    "open_eletronico": int((open_mask & ctr["采购方式"].str.contains("Eletrôn", na=False)).sum()),
    "open_srp": int((open_mask & ctr["价格登记"].eq("是")).sum()),
    "open_credenciamento": int((open_mask & ctr["长期机会"].eq("是")).sum()),
    "foreign_direct_contracts": int(con["is_foreign_direct"].sum()),
    "foreign_direct_pct": round(100*con["is_foreign_direct"].mean(), 4),
    "pj_value_share_pct": round(100*pj.get("total", 0)/con["_cv"].sum(), 2),
    "localized_mnc_suppliers": int(loc["中标供应商"].nunique()),
    "match_lifecycle_records": int(len(m)),
}

OUT.joinpath("stats2.json").write_text(json.dumps(J(R), ensure_ascii=False, indent=2), encoding="utf-8")
print("Saved stats2.json")
print(f"\n[匹配] 合同{R['match']['n_contracts_total']:,} 中可匹配招标 {R['match']['n_matched']:,} 条 ({R['match']['match_rate_pct']}%)")
print("[匹配] 维度分布:", [(r['主维度'], r['n']) for r in R['match_by_dimension']])
print(f"\n[本地化] 中标供应商 {R['localization']['n_unique_suppliers']:,} 家")
print("  主体:", [(r['主体'], r['n'], f"{(r['total'] or 0)/1e8:.1f}亿") for r in by_tipo])
print(f"  外企直接: {R['localization']['foreign_direct']['n_contracts']} 笔 / {R['localization']['foreign_direct']['n_suppliers']} 家")
print(f"  疑似外资本地子公司(启发式): {R['localization']['localized_mnc_heuristic']['n_suppliers']} 家 / "
      f"{R['localization']['localized_mnc_heuristic']['n_contracts']} 笔 / {R['localization']['localized_mnc_heuristic']['value_BRL']/1e6:.1f}M BRL")
print("  本地子公司样例:", [r['中标供应商'][:30] for r in R['localization']['examples_localized_mnc'][:8]])
print("\n[漏斗]")
for s in R["funnel"]["stages"]: print("  ", s["阶段"], "=", f"{s['数量']:,}")
