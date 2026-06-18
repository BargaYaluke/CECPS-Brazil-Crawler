# -*- coding: utf-8 -*-
"""Step12(需求匹配版): 三分类(纯需求契合,无时效/本地化/产能门槛) + 校验输入。
  ① 产品对口(货物供应)    : obj_type=goods 且 fit>=6
  ② 业务对口(服务/工程类)  : 服务/工程/活动类(或服务正则命中) 且 fit>=5
  ③ 高价值候选(不限企业)   : 金额>=500万,按金额取前30(全量830条,截断保持表可用)"""
import sys, io, json, re, math
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

comp = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
prof = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
scored = json.loads((OUT/"scored_needfit.json").read_text("utf-8"))

CAT3_VAL_WAN = 500
CAT3_TOPN = 30

def isnan(v): return v is None or (isinstance(v, float) and math.isnan(v))
SVC = re.compile(r"(vale|cart[aã]o|benef[ií]cio|consignad|institui[cç][aã]o financeira|"
                 r"presta[cç][aã]o de servi|loca[cç][aã]o|manuten[cç][aã]o|配送服务|运营|维护服务|"
                 r"servi[cç]os de|餐补|福利|工资扣款)", re.I)
def svc_or_lic(z):
    return z.get("obj_type") in ("service","event","works") or bool(SVC.search(str(z.get("obj_pt") or "")))

cat1, cat2 = [], []
for cid, v in scored.items():
    if not v or v.get("_error"): continue
    for z in (v.get("scored") or []):
        fit = z.get("fit", 0)
        rec = {"cid":cid, "pncp":z.get("pncp_id"), "梗概":z.get("obj_zh") or z.get("obj_pt"),
               "金额万":("预算保密" if z.get("sigilo") else z.get("valor_wan")),
               "dias":(None if isnan(z.get("dias")) else z.get("dias")),
               "prazo":z.get("prazo_status"), "类型":z.get("obj_type"),
               "采购方式":z.get("modalidade"), "州":z.get("uf"), "机构":z.get("orgao"),
               "srp":z.get("srp_bool"), "lt":z.get("lt_bool"), "link":z.get("link"),
               "action":z.get("action"), "reason":z.get("reason"), "fit":fit}
        if z.get("obj_type") == "goods" and fit >= 6:
            cat1.append(rec)
        elif fit >= 5 and svc_or_lic(z):
            cat2.append(rec)

def dedup(rows):
    best = {}
    for r in rows:
        k = (r["cid"], r["pncp"])
        if k not in best or r["fit"] > best[k]["fit"]:
            best[k] = r
    return list(best.values())
cat1, cat2 = dedup(cat1), dedup(cat2)

# ③ 全池高价值 top30(不限企业、不筛时效)
tenders = json.loads((OUT/"tenders_needfit.json").read_text("utf-8"))
used = {r["pncp"] for r in cat1} | {r["pncp"] for r in cat2}
big = [t for t in tenders if not isnan(t.get("valor_wan")) and t["valor_wan"] >= CAT3_VAL_WAN
       and t["pncp_id"] not in used]
big.sort(key=lambda x: -x["valor_wan"])
n_all = len(big)
cat3 = [{"pncp":t["pncp_id"], "梗概":t.get("obj_zh") or t.get("obj_pt"), "金额万":t["valor_wan"],
         "dias":(None if isnan(t.get("dias")) else t.get("dias")), "prazo":t.get("prazo_status"),
         "行业":norm_industry(t.get("dim")) or "(未分类)", "采购方式":t.get("modalidade"),
         "州":t.get("uf"), "机构":t.get("orgao"), "srp":t.get("srp_bool"), "lt":t.get("lt_bool"),
         "link":t.get("link")} for t in big[:CAT3_TOPN]]

def clean(o):
    if isinstance(o, float) and math.isnan(o): return None
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    return o

indir = OUT/"cat_verify_in_nf"; indir.mkdir(exist_ok=True)
(OUT/"cat_verdicts_nf").mkdir(exist_ok=True)
def industry_of(cid):
    return norm_industry(comp[cid].get("行业")) or norm_industry(prof.get(cid,{}).get("inferred_industry")) or "(未分类)"
cids = sorted(set(r["cid"] for r in cat1) | set(r["cid"] for r in cat2))
for cid in cids:
    c = comp[cid]; p = prof.get(cid, {})
    need = c.get("出海需求")
    need = (str(need).strip()[:500] if need and not isinstance(need, float) else None)
    payload = {"cid":cid, "公司":c["公司简称"], "行业":industry_of(cid),
        "主营概括":p.get("biz_summary"), "产品线":p.get("product_lines"),
        "是否实物供应商":p.get("is_goods_supplier"),
        "出海需求":need, "需求对齐度":p.get("need_alignment"), "需求对齐说明":p.get("need_note"),
        "cat1":[{"pncp":r["pncp"],"梗概":str(r["梗概"])[:180],"类型":r["类型"],"fit":r["fit"]} for r in cat1 if r["cid"]==cid],
        "cat2":[{"pncp":r["pncp"],"梗概":str(r["梗概"])[:180],"类型":r["类型"],"fit":r["fit"]} for r in cat2 if r["cid"]==cid]}
    (indir/f"{cid}.json").write_text(json.dumps(clean(payload), ensure_ascii=False, indent=1), encoding="utf-8")

(OUT/"cat1_needfit.json").write_text(json.dumps(clean(cat1), ensure_ascii=False, indent=1), encoding="utf-8")
(OUT/"cat2_needfit.json").write_text(json.dumps(clean(cat2), ensure_ascii=False, indent=1), encoding="utf-8")
(OUT/"cat3_needfit.json").write_text(json.dumps(clean(cat3), ensure_ascii=False, indent=1), encoding="utf-8")
print(f"① 产品对口 {len(cat1)}配对/{len(set(r['cid'] for r in cat1))}家/{len(set(r['pncp'] for r in cat1))}标")
print(f"② 业务对口 {len(cat2)}配对/{len(set(r['cid'] for r in cat2))}家")
print(f"③ 高价值候选 取前{len(cat3)} (全量≥{CAT3_VAL_WAN}万共{n_all}条)")
print(f"待校验企业: {len(cids)}")
print(json.dumps(cids, ensure_ascii=False))
