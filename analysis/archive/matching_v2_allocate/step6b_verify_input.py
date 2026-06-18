# -*- coding: utf-8 -*-
"""Step6b: 为对抗校验 Workflow 准备自包含输入(高/中重要度企业 + 其推荐匹配)。"""
import sys, io, json, math
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT

def clean(o):
    if isinstance(o, float) and math.isnan(o):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
matches   = json.loads((OUT/"matches.json").read_text("utf-8"))

# 仅校验 高(1)/中(2) 重要度、且非备选 的匹配
by_cid = {}
for r in matches:
    if companies[r["cid"]]["imp_rank"] in (1, 2) and not r.get("备选"):
        by_cid.setdefault(r["cid"], []).append(r)

payload = []
for cid, rows in by_cid.items():
    c = companies[cid]; p = profiles.get(cid, {})
    payload.append({
        "cid": cid,
        "公司": c["公司简称"], "全称": c.get("公司全称"),
        "重要程度": c["重要程度"], "行业": c["行业"], "本地化状态": c["本地化状态"],
        "主营概括": p.get("biz_summary"), "产品线": p.get("product_lines"),
        "产能档位": p.get("capacity_tier"), "单标上限万元": p.get("value_cap_wan_cny"),
        "matches": [{
            "pncp": r["PNCP编号"], "梗概": str(r["竞标中文梗概"])[:200],
            "金额万CNY": r["金额万CNY"], "剩余天数": r["剩余天数"], "类型": r["标的类型"],
            "维度": r["行业"], "契合分": r["契合分"], "可行性分": r["可行性分"],
            "时效分": r["时效分"], "时效渠道": r["时效渠道"],
        } for r in sorted(rows, key=lambda x: x["推荐优先级"])],
    })

payload = clean(payload)
(OUT/"verify_input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
(OUT/"verdicts").mkdir(exist_ok=True)
indir = OUT/"verify_in"; indir.mkdir(exist_ok=True)
for d in payload:                       # 每家一个小文件,校验智能体只读自己的
    (indir/f"{d['cid']}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
cids = [d["cid"] for d in payload]
(OUT/"verify_cids.json").write_text(json.dumps(cids, ensure_ascii=False), encoding="utf-8")
print(f"校验企业 {len(payload)} 家, 匹配条目 {sum(len(d['matches']) for d in payload)}")
print("per-company 输入目录:", indir)
print("cids:", cids[:10], "...")
