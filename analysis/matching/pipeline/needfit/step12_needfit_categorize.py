# -*- coding: utf-8 -*-
"""Step12(需求匹配版·按匹配强度分三类): 纯需求契合,无时效/本地化/产能门槛。
  ① 直接对口(强匹配)   : fit 8-10 —— 企业产品/服务就是标的所采购
  ② 基本对口(中匹配)   : fit 6-7  —— 基本对口,需小幅适配或本地资质
  ③ 同行业相关(弱匹配)  : fit 4-5  —— 同行业大类,产品不直接对口(需求情报)
  剔除: need_alignment==错配 的企业整体剔除;fit<4 丢弃。
  每企业每档设上限(按 fit→金额 取优),保证覆盖全部企业又控制表规模。"""
import sys, io, json, math
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import OUT, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

comp = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
prof = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
scored = json.loads((OUT/"scored_needfit.json").read_text("utf-8"))

CAP1, CAP2, CAP3 = 6, 4, 3       # 每企业每档上限
def isnan(v): return v is None or (isinstance(v, float) and math.isnan(v))
def tier(f):
    if f >= 8: return 1
    if f >= 6: return 2
    if f >= 4: return 3
    return 0
def industry_of(cid):
    return norm_industry(comp[cid].get("行业")) or norm_industry(prof.get(cid,{}).get("inferred_industry")) or "(未分类)"
def vsort(z):                      # 金额排序键(保密/缺失置后)
    v = z.get("valor_wan")
    return v if (v is not None and not (isinstance(v,float) and math.isnan(v))) else -1

cat1, cat2, cat3 = [], [], []
skip_err = 0
for cid, v in scored.items():
    if not v or v.get("_error"): continue
    na = prof.get(cid, {}).get("need_alignment")
    if na == "错配":               # 需求方向与对巴供货错配 → 整体剔除
        skip_err += 1; continue
    buckets = {1: [], 2: [], 3: []}
    seen = set()
    for z in (v.get("scored") or []):
        f = z.get("fit", 0); tg = tier(f)
        if tg == 0: continue
        if z.get("pncp_id") in seen: continue
        seen.add(z.get("pncp_id"))
        buckets[tg].append(z)
    for tg, cap, bucket in ((1, CAP1, cat1), (2, CAP2, cat2), (3, CAP3, cat3)):
        zs = sorted(buckets[tg], key=lambda z: (-z.get("fit",0), -vsort(z)))[:cap]
        for z in zs:
            bucket.append({"cid":cid, "pncp":z.get("pncp_id"),
                "梗概":z.get("obj_zh") or z.get("obj_pt"),
                "金额万":("预算保密" if z.get("sigilo") else z.get("valor_wan")),
                "dias":(None if isnan(z.get("dias")) else z.get("dias")),
                "prazo":z.get("prazo_status"), "类型":z.get("obj_type"),
                "采购方式":z.get("modalidade"), "州":z.get("uf"), "机构":z.get("orgao"),
                "srp":z.get("srp_bool"), "lt":z.get("lt_bool"), "link":z.get("link"),
                "action":z.get("action"), "reason":z.get("reason"),
                "type_match":z.get("type_match"), "fit":z.get("fit",0),
                "core_capability":z.get("core_capability"), "capability_gap":z.get("capability_gap")})

# 校验输入: ①②(直接/基本对口)的企业
def clean(o):
    if isinstance(o, float) and math.isnan(o): return None
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    return o
indir = OUT/"cat_verify_in_nf"; indir.mkdir(exist_ok=True)
(OUT/"cat_verdicts_nf").mkdir(exist_ok=True)
# 清空旧校验输入,避免脏数据
for old in indir.glob("*.json"): old.unlink()
cids = sorted(set(r["cid"] for r in cat1) | set(r["cid"] for r in cat2))
for cid in cids:
    c = comp[cid]; p = prof.get(cid, {})
    need = c.get("出海需求")
    need = (str(need).strip()[:500] if need and not isinstance(need, float) else None)
    payload = {"cid":cid, "公司":c["公司简称"], "行业":industry_of(cid),
        "主营概括":p.get("biz_summary"), "产品线":p.get("product_lines"),
        "是否实物供应商":p.get("is_goods_supplier"),
        "出海需求":need, "需求对齐度":p.get("need_alignment"), "需求对齐说明":p.get("need_note"),
        "cat1":[{"pncp":r["pncp"],"梗概":str(r["梗概"])[:180],"类型":r["类型"],"fit":r["fit"],"核心能力":r.get("core_capability"),"能力缺口":r.get("capability_gap")} for r in cat1 if r["cid"]==cid],
        "cat2":[{"pncp":r["pncp"],"梗概":str(r["梗概"])[:180],"类型":r["类型"],"fit":r["fit"],"核心能力":r.get("core_capability"),"能力缺口":r.get("capability_gap")} for r in cat2 if r["cid"]==cid]}
    (indir/f"{cid}.json").write_text(json.dumps(clean(payload), ensure_ascii=False, indent=1), encoding="utf-8")

(OUT/"cat1_needfit.json").write_text(json.dumps(clean(cat1), ensure_ascii=False, indent=1), encoding="utf-8")
(OUT/"cat2_needfit.json").write_text(json.dumps(clean(cat2), ensure_ascii=False, indent=1), encoding="utf-8")
(OUT/"cat3_needfit.json").write_text(json.dumps(clean(cat3), ensure_ascii=False, indent=1), encoding="utf-8")
(OUT/"cat_cids_nf.json").write_text(json.dumps(cids, ensure_ascii=False), encoding="utf-8")
import collections
print(f"剔除需求错配企业: {skip_err}")
print(f"① 直接对口(fit8-10) {len(cat1)}配对/{len(set(r['cid'] for r in cat1))}家")
print(f"② 基本对口(fit6-7)  {len(cat2)}配对/{len(set(r['cid'] for r in cat2))}家")
print(f"③ 同行业相关(fit4-5) {len(cat3)}配对/{len(set(r['cid'] for r in cat3))}家")
print(f"合计 {len(cat1)+len(cat2)+len(cat3)} 行 | 涉及企业 {len(set(r['cid'] for r in cat1+cat2+cat3))}")
print(f"待校验企业(①②): {len(cids)}")
print("类型分布①:", dict(collections.Counter(r['类型'] for r in cat1)))
