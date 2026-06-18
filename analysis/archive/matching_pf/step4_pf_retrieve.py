# -*- coding: utf-8 -*-
"""Step4(纯契合版): 候选召回 —— 只按产品/业务相关性(关键词命中+同维度),
   完全不考虑时效与本地化可行性;含已过期与工程标。输出 candidates_pf.json。"""
import sys, io, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT, kw_hits_pt, kw_hits_zh, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

TOPK = 22

companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
tenders   = json.loads((OUT/"tenders_all.json").read_text("utf-8"))
print(f"企业 {len(companies)} | 画像 {len(profiles)} | 全量招标池 {len(tenders)}")

candidates = {}
stats = {"no_profile": 0, "no_cand": 0, "ok": 0}
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
    for t in tenders:
        ph = kw_hits_pt(t.get("obj_pt"), kws_pt)
        zh = kw_hits_zh(t.get("obj_zh"), kws_zh)
        n_hits = len(set(ph)) + len(set(zh))
        same_dim = bool(t.get("dim") and t["dim"] == industry)
        conf = float(t.get("dim_conf") or 0.5)
        if n_hits > 0:
            rel = 0.5 + 0.1 * min(n_hits, 5)
            if same_dim:
                rel = min(1.0, rel + 0.05)
            pre = rel + conf * 0.04
            kw_cands.append((pre, n_hits, sorted(set(ph)), sorted(set(zh)), t))
        elif same_dim:
            filler.append((0.30 + conf * 0.04, 0, [], [], t))

    kw_cands.sort(key=lambda x: -x[0])
    filler.sort(key=lambda x: -x[0])
    top = kw_cands[:TOPK]
    if len(top) < TOPK:
        top += filler[:TOPK - len(top)]

    if not top:
        stats["no_cand"] += 1
        candidates[cid] = []
        continue
    candidates[cid] = [{
        "pre_score": round(s[0], 4), "kw_n": s[1], "hit_pt": s[2], "hit_zh": s[3],
        **{k: s[4].get(k) for k in ("pncp_id","obj_zh","obj_pt","modalidade","dim","dim_raw",
            "valor_wan","sigilo","orgao","esfera","uf","deadline","dias","prazo_status",
            "srp_bool","lt_bool","obj_type","link")},
    } for s in top]
    stats["ok"] += 1

(OUT/"candidates_pf.json").write_text(json.dumps(candidates, ensure_ascii=False), encoding="utf-8")
ncand = [len(v) for v in candidates.values() if v]
nkw = sum(1 for v in candidates.values() for x in v if x["kw_n"] > 0)
print("召回完成:", stats)
print(f"有候选企业: {len(ncand)} | 平均候选数: {sum(ncand)/max(len(ncand),1):.1f} | 关键词命中候选: {nkw}")
