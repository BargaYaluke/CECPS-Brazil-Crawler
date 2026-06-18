# -*- coding: utf-8 -*-
"""Step4c: 汇总 Claude 核验裁决(verify_out/*.json)→ 每桶『经核验名册』。
名册=该桶 DeepSeek 候选中 未被剔除(real=true 且 overseas∈{强,有,弱}) 的企业,
附核验后的 体量/能力/出海强度/巴西/证据;按 出海强度→巴西→原序 排。
产物: outputs/rosters.json {bucket_key: [company,...]}  + verdicts_merged.json
"""
import sys, io, json
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = Path(__file__).resolve().parent / "outputs"
VOUT = OUT / "verify_out"

pools = json.loads((OUT/"candidate_pools.json").read_text("utf-8"))

# ── 合并裁决(按公司名)──
verd = {}
missing = []
manifest = json.loads((OUT/"verify_manifest.json").read_text("utf-8"))
for m in manifest:
    f = VOUT / f"{m['batch_id']}.json"
    if not f.exists():
        missing.append(m["batch_id"]); continue
    try:
        d = json.loads(f.read_text("utf-8"))
    except Exception as e:
        missing.append(m["batch_id"] + f"(坏:{e})"); continue
    for v in d.get("verdicts", []):
        nm = str(v.get("name", "")).strip()
        if nm:
            verd[nm] = v
print(f"裁决覆盖公司: {len(verd)} | 缺/坏批: {len(missing)} {missing[:10] if missing else ''}")

(OUT/"verdicts_merged.json").write_text(json.dumps(verd, ensure_ascii=False, indent=1), encoding="utf-8")

OVS_RANK = {"强": 3, "有": 2, "弱": 1, "未知": 0, "无": -1}

def match_verdict(name):
    """裁决按公司名匹配;允许轻微出入(去空格/括号后缀)。"""
    if name in verd:
        return verd[name]
    key = name.replace(" ", "")
    for k, v in verd.items():
        if k.replace(" ", "") == key:
            return v
    # 子串兜底(规范全称 vs 简称)
    for k, v in verd.items():
        kk = k.replace(" ", "")
        if kk and (kk in key or key in kk) and abs(len(kk) - len(key)) <= 4:
            return v
    return None

rosters = {}
stat = {"kept": 0, "dropped": 0, "no_verdict": 0}
for bkey, pv in pools.items():
    roster = []
    for c in pv["candidates"]:
        v = match_verdict(c["name"])
        if v is None:
            # 无裁决:保守保留但标注未核验(避免整桶空)
            stat["no_verdict"] += 1
            roster.append({**c, "real": None, "overseas": "未核验", "brazil": False,
                           "evidence": c.get("overseas") or "未核验", "ovs_rank": 0, "verified": False})
            continue
        ovs = v.get("overseas", "未知")
        if v.get("drop") or v.get("real") is False or ovs in ("无",):
            stat["dropped"] += 1
            continue
        stat["kept"] += 1
        roster.append({
            "name": v.get("name") or c["name"],
            "brief": c.get("brief", ""),
            "cap": v.get("cap") or c.get("cap") or "综合",
            "size": v.get("size") or c.get("size") or "中",
            "overseas": ovs,
            "brazil": bool(v.get("brazil")),
            "evidence": (v.get("evidence") or "").strip()[:40] or "—",
            "fit": c.get("fit", ""),
            "ovs_rank": OVS_RANK.get(ovs, 0),
            "verified": True,
        })
    # 排序:出海强度 → 巴西 → 已核验 → 原序(稳定)
    roster_sorted = sorted(roster, key=lambda r: (-r["ovs_rank"], not r["brazil"], not r.get("verified")))
    rosters[bkey] = roster_sorted[:10]

(OUT/"rosters.json").write_text(json.dumps(rosters, ensure_ascii=False, indent=1), encoding="utf-8")
empty = [k for k, v in rosters.items() if not v]
nlist = [len(v) for v in rosters.values()]
print(f"名册: {len(rosters)} 桶,平均 {sum(nlist)/max(len(nlist),1):.1f} 家;空桶 {len(empty)} {empty[:10]}")
print("裁决统计:", stat)
# 出海强度分布(名册内)
from collections import Counter
allc = [r for v in rosters.values() for r in v]
print("名册内出海强度:", dict(Counter(r["overseas"] for r in allc)))
print("名册内巴西布局:", sum(1 for r in allc if r["brazil"]), "/", len(allc))
