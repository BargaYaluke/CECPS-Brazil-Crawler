# -*- coding: utf-8 -*-
"""Step8(纯契合版): 三分类 —— 无时效/本地化门槛,只看契合:
  第一类 产品直接对口(货物)        : obj_type=goods & fit>=6, 每企业取 top5
  第二类 业务相邻(需本地资质/服务类): 服务/工程/牌照类 & fit>=5, 每企业取 top3
  第三类 高价值候选(不限企业)      : 全量池(含过期) 金额>=500万, 取 top50
  并写 Cat1/Cat2 校验输入(含出海需求)。"""
import sys, io, json, re, math
import pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT, ROOT, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

comp = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
prof = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
scored = json.loads((OUT/"scored_pf.json").read_text("utf-8"))

CAT3_VAL_WAN = 500
CAT3_TOP = 50
TOP1, TOP2 = 5, 3

def isnan(v): return v is None or (isinstance(v, float) and math.isnan(v))
SVC = re.compile(r"(vale|cart[aã]o|benef[ií]cio|consignad|institui[cç][aã]o financeira|"
                 r"presta[cç][aã]o de servi|loca[cç][aã]o|manuten[cç][aã]o|配送服务|运营|维护服务|"
                 r"servi[cç]os de|餐补|福利|工资扣款)", re.I)
def svc_or_lic(z):
    return z.get("obj_type") in ("service","event","works") or bool(SVC.search(str(z.get("obj_pt") or "")))

def mk_rec(cid, z):
    dias = z.get("dias")
    return {"cid":cid, "pncp":z.get("pncp_id"), "梗概":z.get("obj_zh") or z.get("obj_pt"),
            "金额万":("预算保密" if z.get("sigilo") else z.get("valor_wan")),
            "dias":(None if isnan(dias) else dias), "时效状态":z.get("prazo_status"),
            "类型":z.get("obj_type"), "采购方式":z.get("modalidade"), "州":z.get("uf"),
            "机构":z.get("orgao"), "srp":z.get("srp_bool"), "lt":z.get("lt_bool"),
            "link":z.get("link"), "channel":z.get("suggestion"), "reason":z.get("reason"),
            "risk":z.get("risk"), "fit":z.get("fit", 0)}

cat1, cat2 = [], []
for cid, v in scored.items():
    if not v or v.get("_error"): continue
    rows = v.get("scored") or []
    c1 = [z for z in rows if z.get("obj_type") == "goods" and z.get("fit", 0) >= 6]
    c2 = [z for z in rows if svc_or_lic(z) and z.get("fit", 0) >= 5]
    c1.sort(key=lambda z: -z.get("fit", 0))
    c2.sort(key=lambda z: -z.get("fit", 0))
    # goods 也可能落在 c2(SVC正则命中),去重:c1 优先
    used = {z.get("pncp_id") for z in c1[:TOP1]}
    c2 = [z for z in c2 if z.get("pncp_id") not in used]
    cat1 += [mk_rec(cid, z) for z in c1[:TOP1]]
    cat2 += [mk_rec(cid, z) for z in c2[:TOP2]]

# 去重(同一对只留一次,取fit高)
def dedup(rows):
    best={}
    for r in rows:
        k=(r["cid"],r["pncp"])
        if k not in best or r["fit"]>best[k]["fit"]: best[k]=r
    return list(best.values())
cat1, cat2 = dedup(cat1), dedup(cat2)

# 第三类:全量招标(含过期/服务/工程) 高价值 top50,排除已占用
used = {r["pncp"] for r in cat1} | {r["pncp"] for r in cat2}
t = pd.read_excel(ROOT/"data/exports/report.xlsx", sheet_name="招标主表")
d = pd.to_numeric(t["剩余天数"], errors="coerce"); val = pd.to_numeric(t["预估金额(CNY)"], errors="coerce")
mask = (val >= CAT3_VAL_WAN*1e4) & (val <= 5e8)
cat3 = []
sub = t[mask].copy(); sub["_v"] = val[mask]
sub = sub.sort_values("_v", ascending=False)
for _, r in sub.iterrows():
    if r["PNCP编号"] in used: continue
    if len(cat3) >= CAT3_TOP: break
    dias = d[r.name]
    cat3.append({"pncp":r["PNCP编号"], "梗概":r["标的(中文梗概)"],
        "金额万":round(float(val[r.name])/1e4,1),
        "dias":(None if pd.isna(dias) else int(dias)), "时效状态":r["时效状态"],
        "行业":norm_industry(r.get("主维度")) or "(未分类)", "采购方式":r["采购方式"],
        "州":r["州"], "机构":r["采购机构"], "srp":r["价格登记"]=="是", "lt":r["长期机会"]=="是"})

# 校验输入
def clean(o):
    if isinstance(o,float) and math.isnan(o): return None
    if isinstance(o,dict): return {k:clean(v) for k,v in o.items()}
    if isinstance(o,list): return [clean(v) for v in o]
    return o
indir = OUT/"cat_verify_in_pf"; indir.mkdir(exist_ok=True)
(OUT/"cat_verdicts_pf").mkdir(exist_ok=True)
def industry_of(cid):
    return norm_industry(comp[cid].get("行业")) or norm_industry(prof.get(cid,{}).get("inferred_industry")) or "(未分类)"
cids = sorted(set(r["cid"] for r in cat1) | set(r["cid"] for r in cat2))
for cid in cids:
    c=comp[cid]; p=prof.get(cid,{})
    need = c.get("出海需求")
    need = (str(need).strip()[:500] if need and not isinstance(need, float) else None)
    payload={"cid":cid,"公司":c["公司简称"],"行业":industry_of(cid),
        "主营概括":p.get("biz_summary"),"产品线":p.get("product_lines"),
        "出海需求":need,"需求对齐度":p.get("need_alignment"),"需求对齐说明":p.get("need_note"),
        "cat1":[{"pncp":r["pncp"],"梗概":str(r["梗概"])[:180],"金额万":r["金额万"],"类型":r["类型"],"fit":r["fit"]} for r in cat1 if r["cid"]==cid],
        "cat2":[{"pncp":r["pncp"],"梗概":str(r["梗概"])[:180],"金额万":r["金额万"],"类型":r["类型"],"fit":r["fit"]} for r in cat2 if r["cid"]==cid]}
    (indir/f"{cid}.json").write_text(json.dumps(clean(payload),ensure_ascii=False,indent=1),encoding="utf-8")

(OUT/"cat1_pf.json").write_text(json.dumps(clean(cat1),ensure_ascii=False,indent=1),encoding="utf-8")
(OUT/"cat2_pf.json").write_text(json.dumps(clean(cat2),ensure_ascii=False,indent=1),encoding="utf-8")
(OUT/"cat3_pf.json").write_text(json.dumps(clean(cat3),ensure_ascii=False,indent=1),encoding="utf-8")
(OUT/"cat_cids_pf.json").write_text(json.dumps(cids,ensure_ascii=False),encoding="utf-8")
print(f"第一类 {len(cat1)}配对/{len(set(r['cid'] for r in cat1))}家/{len(set(r['pncp'] for r in cat1))}标")
print(f"第二类 {len(cat2)}配对/{len(set(r['cid'] for r in cat2))}家")
print(f"第三类 {len(cat3)}标(高价值≥{CAT3_VAL_WAN}万,不限时效,top{CAT3_TOP})")
print(f"待校验企业(Cat1∪Cat2): {len(cids)}")
print(json.dumps(cids, ensure_ascii=False))
