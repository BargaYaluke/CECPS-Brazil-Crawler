# -*- coding: utf-8 -*-
"""Step8: 三分类(均过时效门槛)+ 准备 Cat1/Cat2 校验输入。
  第一类 核心匹配  : 产品对口货物 + 时间够本地化(dias>=lead 或无截止),many-to-many
  第二类 牌照后可争取: 业务相邻但需本地牌照/资质 + 更长窗口(dias>=365 或无截止)
  第三类 高价值候选 : 高价值+长截止的标(不限品类/含服务工程),不绑定企业
"""
import sys, io, json, re, math
import pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import OUT, ROOT, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

comp = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
prof = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
scored = json.loads((OUT/"scored.json").read_text("utf-8"))
verd = json.loads((OUT/"verdicts.json").read_text("utf-8")) if (OUT/"verdicts.json").exists() else {}

CAT3_VAL_WAN = 500     # 第三类高价值门槛(万CNY)
CAT3_DIAS = 90         # 第三类长截止门槛(天)
CAT2_DIAS = 365        # 第二类更充裕窗口(天)

def lead(c, p):
    if c["本地化状态"] == "已在巴西": return 14
    if c["本地化状态"] == "葡语区(较易)": return 90
    return {"低":90, "中":150, "高":240}.get((p or {}).get("localization_need"), 240)
def isnan(v): return v is None or (isinstance(v, float) and math.isnan(v))
def openend(z): return isnan(z.get("dias"))
SVC = re.compile(r"(vale|cart[aã]o|benef[ií]cio|consignad|institui[cç][aã]o financeira|"
                 r"presta[cç][aã]o de servi|loca[cç][aã]o|manuten[cç][aã]o|配送服务|运营|维护服务|"
                 r"servi[cç]os de|餐补|福利|工资扣款)", re.I)
def svc_or_lic(z):
    return z.get("obj_type") in ("service","event") or bool(SVC.search(str(z.get("obj_pt") or "")))
def industry_of(cid):
    return norm_industry(comp[cid].get("行业")) or norm_industry(prof.get(cid,{}).get("inferred_industry")) or "(未分类)"

cat1, cat2 = [], []
for cid, v in scored.items():
    if not v or v.get("_error"): continue
    c = comp[cid]; p = prof.get(cid, {}); L = lead(c, p)
    for z in (v.get("scored") or []):
        dias = z.get("dias"); fit = z.get("fit",0); feas = z.get("feasibility",0)
        oe = openend(z)
        tf1 = oe or (not isnan(dias) and dias >= L)
        long2 = oe or (not isnan(dias) and dias >= CAT2_DIAS)
        rej = verd.get(f"{cid}|{z.get('pncp_id')}", {}).get("结论") == "剔除"
        rec = {"cid":cid, "pncp":z.get("pncp_id"), "梗概":z.get("obj_zh") or z.get("obj_pt"),
               "金额万":("预算保密" if z.get("sigilo") else z.get("valor_wan")),
               "dias":(None if isnan(dias) else dias), "类型":z.get("obj_type"),
               "采购方式":z.get("modalidade"), "州":z.get("uf"), "机构":z.get("orgao"),
               "srp":z.get("srp_bool"), "lt":z.get("lt_bool"), "link":z.get("link"),
               "channel":z.get("channel"), "reason":z.get("reason"), "fit":fit,
               "core_capability":z.get("core_capability"), "capability_gap":z.get("capability_gap")}
        if tf1 and z.get("obj_type")=="goods" and fit>=6 and feas>=5 and not rej:
            cat1.append(rec)
        elif long2 and fit>=5 and (svc_or_lic(z) or rej):
            cat2.append(rec)

# 去重(同一对只留一次,取fit高)
def dedup(rows):
    best={}
    for r in rows:
        k=(r["cid"],r["pncp"])
        if k not in best or r["fit"]>best[k]["fit"]: best[k]=r
    return list(best.values())
cat1, cat2 = dedup(cat1), dedup(cat2)

# 第三类:全量招标(含服务/工程)里 高价值+长截止,排除已在Cat1/Cat2的标
used = {r["pncp"] for r in cat1} | {r["pncp"] for r in cat2}
t = pd.read_excel(ROOT/"data/exports/report.xlsx", sheet_name="招标主表")
d = pd.to_numeric(t["剩余天数"], errors="coerce"); val = pd.to_numeric(t["预估金额(CNY)"], errors="coerce")
mask = (~t["时效状态"].eq("已过期") & (d.fillna(9999)>=0) & (val.fillna(0)<=5e8)
        & (val>=CAT3_VAL_WAN*1e4) & ((d>=CAT3_DIAS) | d.isna()))
cat3=[]
for _, r in t[mask].iterrows():
    if r["PNCP编号"] in used: continue
    cat3.append({"pncp":r["PNCP编号"], "梗概":r["标的(中文梗概)"],
        "金额万":round(float(val[r.name])/1e4,1) if not pd.isna(val[r.name]) else None,
        "dias":(None if pd.isna(d[r.name]) else int(d[r.name])),
        "行业":norm_industry(r.get("主维度")) or "(未分类)", "采购方式":r["采购方式"],
        "州":r["州"], "机构":r["采购机构"], "srp":r["价格登记"]=="是", "lt":r["长期机会"]=="是"})
cat3.sort(key=lambda x: -(x["金额万"] or 0))

# 写校验输入(Cat1∪Cat2 的企业)
def clean(o):
    if isinstance(o,float) and math.isnan(o): return None
    if isinstance(o,dict): return {k:clean(v) for k,v in o.items()}
    if isinstance(o,list): return [clean(v) for v in o]
    return o
indir = OUT/"cat_verify_in"; indir.mkdir(exist_ok=True)
(OUT/"cat_verdicts").mkdir(exist_ok=True)
cids = sorted(set(r["cid"] for r in cat1) | set(r["cid"] for r in cat2))
for cid in cids:
    c=comp[cid]; p=prof.get(cid,{})
    payload={"cid":cid,"公司":c["公司简称"],"行业":industry_of(cid),"本地化状态":c["本地化状态"],
        "主营概括":p.get("biz_summary"),"产品线":p.get("product_lines"),"单标上限万元":p.get("value_cap_wan_cny"),
        "出海需求":(str(c.get("出海需求")).strip()[:500]
                if c.get("出海需求") and not isinstance(c.get("出海需求"), float) else None),
        "需求对齐度":p.get("need_alignment"),"需求对齐说明":p.get("need_note"),
        "cat1":[{"pncp":r["pncp"],"梗概":str(r["梗概"])[:180],"金额万":r["金额万"],"剩余天数":r["dias"],"类型":r["类型"],"核心能力":r.get("core_capability"),"能力缺口":r.get("capability_gap")} for r in cat1 if r["cid"]==cid],
        "cat2":[{"pncp":r["pncp"],"梗概":str(r["梗概"])[:180],"金额万":r["金额万"],"剩余天数":r["dias"],"类型":r["类型"],"核心能力":r.get("core_capability"),"能力缺口":r.get("capability_gap")} for r in cat2 if r["cid"]==cid]}
    (indir/f"{cid}.json").write_text(json.dumps(clean(payload),ensure_ascii=False,indent=1),encoding="utf-8")

(OUT/"cat1.json").write_text(json.dumps(cat1,ensure_ascii=False,indent=1),encoding="utf-8")
(OUT/"cat2.json").write_text(json.dumps(cat2,ensure_ascii=False,indent=1),encoding="utf-8")
(OUT/"cat3.json").write_text(json.dumps(cat3,ensure_ascii=False,indent=1),encoding="utf-8")
(OUT/"cat_cids.json").write_text(json.dumps(cids,ensure_ascii=False),encoding="utf-8")
print(f"第一类 {len(cat1)}配对/{len(set(r['cid'] for r in cat1))}家/{len(set(r['pncp'] for r in cat1))}标")
print(f"第二类 {len(cat2)}配对/{len(set(r['cid'] for r in cat2))}家")
print(f"第三类 {len(cat3)}标(高价值≥{CAT3_VAL_WAN}万 且 剩≥{CAT3_DIAS}天/无截止)")
print(f"待校验企业(Cat1∪Cat2): {len(cids)}")
print(json.dumps(cids, ensure_ascii=False))
