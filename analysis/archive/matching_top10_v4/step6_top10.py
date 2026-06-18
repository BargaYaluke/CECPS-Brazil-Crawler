# -*- coding: utf-8 -*-
"""Step6(全覆盖+时效硬门槛版): 每家恰好top-10,按重要程度排序,★首推软独占。
   关键:据本地化周期(6/9基准)做确定性时效门槛 —— 截止过短=本轮不可投,降级为『下一周期·需求情报』。"""
import sys, io, json, math
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

TOPN = 10
companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
scored    = json.loads((OUT/"scored.json").read_text("utf-8"))
verdicts  = json.loads((OUT/"verdicts.json").read_text("utf-8")) if (OUT/"verdicts.json").exists() else {}

def verdict_of(cid, pncp): return verdicts.get(f"{cid}|{pncp}", {}).get("结论")

def lead_days(c, p):
    if c["本地化状态"] == "已在巴西": return 14
    if c["本地化状态"] == "葡语区(较易)": return 90
    return {"低":90, "中":150, "高":240}.get((p or {}).get("localization_need"), 240)

def is_nan(v): return v is None or (isinstance(v, float) and math.isnan(v))

def time_feasible(dias, lead):
    if is_nan(dias): return True          # 无截止/credenciamento常年开放
    return dias >= lead

def industry_of(cid):
    c = companies[cid]
    return norm_industry(c.get("行业")) or norm_industry(profiles.get(cid,{}).get("inferred_industry")) or "(未分类)"

# 分层:时效优先(用户要求:截止过短不现实)
def tier_of(cid, z, tfeas):
    vd = verdict_of(cid, z.get("pncp_id"))
    fit, feas = z.get("fit",0), z.get("feasibility",0)
    if vd == "剔除":
        return 6, "✕不建议(校验剔除)"
    if not tfeas:
        if fit >= 6:
            return 4, "◇下一周期·需求情报(截止过短)"
        return 5, "·低契合(凑数)"
    if vd == "降级":
        return 3, "△可投但高风险(校验降级)"
    if fit >= 6 and feas >= 5:
        return 1, "✓本轮可投"
    if fit >= 5:
        return 2, "○时效可行(联合体/弱契合)"
    return 5, "·低契合(凑数)"

# 1) 每家:先算时效可行,再排序取 top-10(tier 优先,再综合分)
per_company = {}
for cid, c in companies.items():
    v = scored.get(cid); p = profiles.get(cid, {})
    if not v or v.get("_error"):
        per_company[cid] = []; continue
    L = lead_days(c, p)
    lst = v.get("scored") or []
    for z in lst:
        z["_tfeas"] = time_feasible(z.get("dias"), L)
        z["_lead"] = L
        z["_trank"], z["_tlabel"] = tier_of(cid, z, z["_tfeas"])
    lst = sorted(lst, key=lambda z: (z["_trank"], -z.get("final_score",0), -z.get("fit",0)))
    per_company[cid] = lst[:TOPN]

# 2) ★首推 软独占:重要程度优先,优质『可投(tier1-3)』标首推给最重要企业
order = sorted(per_company.keys(),
    key=lambda cid: (companies[cid]["imp_rank"],
                     -(per_company[cid][0]["final_score"] if per_company[cid] else 0)))
claimed = {}
for cid in order:
    rows = per_company[cid]
    if not rows: continue
    ranked = sorted(rows, key=lambda z: (z["_trank"], -z.get("final_score",0)))
    headline = (next((z for z in ranked if z["_trank"] <= 3 and z["pncp_id"] not in claimed), None)
                or next((z for z in ranked if z["_trank"] <= 3), None)
                or next((z for z in ranked if z["_trank"] == 4), None)
                or next((z for z in ranked if z["_trank"] != 6), None)
                or ranked[0])
    hp = headline["pncp_id"]
    if hp not in claimed: claimed[hp] = cid
    for z in rows:
        z["_headline"] = (z["pncp_id"] == hp)
        z["_shared"] = companies[claimed[hp]]["公司简称"] if (z.get("_headline") and claimed.get(hp)!=cid) else ""

# 3) 展平
def fmt_amt(z): return "预算保密" if z.get("sigilo") else z.get("valor_wan")
rows_out = []
for cid in order:
    c = companies[cid]
    rows = sorted(per_company[cid], key=lambda z: (not z.get("_headline"), z["_trank"], -z.get("final_score",0)))
    for prio, z in enumerate(rows, 1):
        rows_out.append({
            "cid": cid, "imp_rank": c["imp_rank"], "重要程度": c["重要程度"],
            "公司简称": c["公司简称"], "公司全称": c.get("公司全称"), "行业": industry_of(cid),
            "本地化状态": c["本地化状态"], "所需本地化周期天": z["_lead"],
            "推荐优先级": prio, "可投性": z["_tlabel"],
            "时效可行": "是" if z["_tfeas"] else "否(截止过短)",
            "首推": "★首推" if z.get("_headline") else "", "共享首推": z.get("_shared",""),
            "竞标中文梗概": z.get("obj_zh") or z.get("obj_pt"),
            "金额万CNY": fmt_amt(z),
            "采购方式": z.get("modalidade"), "标的类型": z.get("obj_type"),
            "截止日期": z.get("deadline"), "剩余天数": z.get("dias"),
            "州": z.get("uf"), "级别": z.get("esfera"), "采购机构": z.get("orgao"),
            "价格登记SRP": "是" if z.get("srp_bool") else "否",
            "长期挂网": "是" if z.get("lt_bool") else "否",
            "契合分": z.get("fit"), "可行性分": z.get("feasibility"), "时效分": z.get("timeliness"),
            "综合分": z.get("final_score"),
            "时效渠道": z.get("timeliness_label"), "落地建议": z.get("channel"),
            "匹配理由": z.get("reason"), "风险": z.get("risk"),
            "PNCP编号": z.get("pncp_id"), "投标链接": z.get("link"),
        })

# 4) 总览
from collections import Counter
overview = []
for cid in order:
    c = companies[cid]; p = profiles.get(cid, {}); rows = per_company[cid]
    tc = Counter(z["_tlabel"] for z in rows)
    overview.append({
        "重要程度": c["重要程度"], "imp_rank": c["imp_rank"], "公司简称": c["公司简称"],
        "公司全称": c.get("公司全称"), "行业": industry_of(cid), "本地化状态": c["本地化状态"],
        "所需本地化周期天": lead_days(c,p),
        "政采契合": p.get("gov_fit"), "产能档位": p.get("capacity_tier"),
        "单标上限万元": p.get("value_cap_wan_cny"), "本地化难度": p.get("localization_need"),
        "本轮可投数": tc.get("✓本轮可投",0)+tc.get("○时效可行(联合体/弱契合)",0),
        "其中高风险": tc.get("△可投但高风险(校验降级)",0),
        "下一周期情报": tc.get("◇下一周期·需求情报(截止过短)",0),
        "校验剔除": tc.get("✕不建议(校验剔除)",0), "低契合凑数": tc.get("·低契合(凑数)",0),
        "首推标的": next((z.get("obj_zh") for z in rows if z.get("_headline")), None),
        "首推可投性": next((z["_tlabel"] for z in rows if z.get("_headline")), None),
        "主营概括": p.get("biz_summary"), "落地提示": p.get("note"),
    })

(OUT/"matches_top10.json").write_text(json.dumps(rows_out, ensure_ascii=False, indent=1), encoding="utf-8")
(OUT/"overview_top10.json").write_text(json.dumps(overview, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"企业 {len(order)} | 行 {len(rows_out)} | 每家=10:", all(len(per_company[cid])==TOPN for cid in order))
print("可投性分布:", dict(Counter(r["可投性"] for r in rows_out)))
print("时效可行行数:", sum(1 for r in rows_out if r["时效可行"]=="是"))
biddable_companies = sum(1 for o in overview if o["本轮可投数"]>0)
print(f"有≥1本轮可投的企业: {biddable_companies}/{len(order)}")
print("各重要程度·本轮可投企业数:",
      dict(Counter(o["重要程度"] for o in overview if o["本轮可投数"]>0)))
