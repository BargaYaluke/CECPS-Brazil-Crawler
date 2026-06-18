# -*- coding: utf-8 -*-
"""Step10(需求匹配版): 全量招标池(不筛时效/不剔工程,仅剔金额>5亿数据坑) + 纯需求契合召回。
   口径: 不关心是否过期、不关心本地化可行性、不关心产能;只看 企业产品/业务+出海需求 ↔ 标的需要。"""
import sys, io, json, sqlite3
import pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import ROOT, OUT, norm_industry, classify_obj, kw_hits_pt, kw_hits_zh

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── 池子 ──
t = pd.read_excel(ROOT/"data/exports/report.xlsx", sheet_name="招标主表")
con = sqlite3.connect(str(ROOT/"data/procurement.db"))
links = pd.read_sql_query(
    "select pncp_id as PNCP编号, link_sistema_origem as 投标链接 from contratacoes", con)
con.close()
t = t.merge(links, on="PNCP编号", how="left")

t["金额CNY"] = pd.to_numeric(t["预估金额(CNY)"], errors="coerce")
t["金额万CNY"] = (t["金额CNY"]/1e4).round(1)
t["预算保密"] = t["金额CNY"].fillna(-1) == 0
t["剩余天数"] = pd.to_numeric(t["剩余天数"], errors="coerce")
t["标的类型"] = t["标的(原文)"].map(classify_obj)
t["行业"] = t["主维度"].map(norm_industry)

n0 = len(t)
keep = ~(t["金额CNY"] > 5e8)          # 只剔数据坑;过期/工程/服务全保留
clean = t[keep].copy()
print(f"全量 {n0} | 剔金额>5亿 {int((~keep).sum())} | 需求匹配池 {len(clean)}")
print("池内时效状态:", clean["时效状态"].value_counts(dropna=False).to_dict())
print("池内标的类型:", clean["标的类型"].value_counts(dropna=False).to_dict())

COLS = {
    "PNCP编号":"pncp_id", "标的(中文梗概)":"obj_zh", "标的(原文)":"obj_pt",
    "采购方式":"modalidade", "行业":"dim", "维度置信度":"dim_conf",
    "金额万CNY":"valor_wan", "预算保密":"sigilo", "采购机构":"orgao", "级别":"esfera",
    "州":"uf", "投标截止":"deadline", "剩余天数":"dias", "时效状态":"prazo_status",
    "价格登记":"srp", "长期机会":"long_term", "标的类型":"obj_type", "投标链接":"link",
}
clean = clean.rename(columns=COLS)[list(COLS.values())].copy()
clean["deadline"] = clean["deadline"].astype(str).str[:10]
clean["srp_bool"] = clean["srp"].eq("是")
clean["lt_bool"] = clean["long_term"].eq("是")
tenders = clean.where(pd.notna(clean), None).to_dict(orient="records")
(OUT/"tenders_needfit.json").write_text(json.dumps(tenders, ensure_ascii=False), encoding="utf-8")

# ── 纯需求契合召回(无时效/价值/类型惩罚项) ──
TOPK, MINCAND = 22, 12
companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))

candidates, stats = {}, {"no_profile":0, "ok":0, "filler":0}
KEEP_T = ("pncp_id","obj_zh","obj_pt","modalidade","dim","dim_conf","valor_wan","sigilo",
          "orgao","esfera","uf","deadline","dias","prazo_status","srp_bool","lt_bool","obj_type","link")
for cid, c in companies.items():
    p = profiles.get(cid)
    if not p or p.get("_error"):
        stats["no_profile"] += 1
        candidates[cid] = []
        continue
    kws_pt = [str(x) for x in (p.get("keywords_pt") or []) if x]
    kws_zh = [str(x) for x in (p.get("keywords_zh") or []) if x]
    industry = norm_industry(c.get("行业")) or norm_industry(p.get("inferred_industry"))
    kw_cands, filler = [], []
    for td in tenders:
        ph = kw_hits_pt(td.get("obj_pt"), kws_pt)
        zh = kw_hits_zh(td.get("obj_zh"), kws_zh)
        n_hits = len(set(ph)) + len(set(zh))
        same_dim = bool(td.get("dim") and td["dim"] == industry)
        conf = float(td.get("dim_conf") or 0.5)
        if n_hits > 0:
            rel = 0.5 + 0.1*min(n_hits, 5) + (0.05 if same_dim else 0)
            kw_cands.append((rel*0.9 + conf*0.1, n_hits, sorted(set(ph)), sorted(set(zh)), td))
        elif same_dim and "Credenciamento" not in str(td.get("modalidade") or ""):
            filler.append((0.30*0.9 + conf*0.1, 0, [], [], td))
    kw_cands.sort(key=lambda x: -x[0])
    top = kw_cands[:TOPK]
    if len(top) < MINCAND:
        filler.sort(key=lambda x: -x[0])
        top += filler[:MINCAND - len(top)]
        stats["filler"] += 1
    candidates[cid] = [{
        "pre_score": round(s[0], 4), "kw_n": s[1], "hit_pt": s[2], "hit_zh": s[3],
        **{k: s[4].get(k) for k in KEEP_T},
    } for s in top]
    stats["ok"] += 1

(OUT/"candidates_needfit.json").write_text(json.dumps(candidates, ensure_ascii=False), encoding="utf-8")
ncand = [len(v) for v in candidates.values() if v]
print("\n召回:", stats, f"| 平均候选 {sum(ncand)/max(len(ncand),1):.1f}")
exp = sum(1 for v in candidates.values() for x in v if x.get("prazo_status") == "已过期")
wk = sum(1 for v in candidates.values() for x in v if x.get("obj_type") == "works")
print(f"候选中含已过期 {exp} 条 / 工程类 {wk} 条 (本版有效)")
