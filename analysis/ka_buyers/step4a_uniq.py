# -*- coding: utf-8 -*-
"""Step4a: 抽取全部桶里的 *唯一* 候选中企(去重),按主 一级行业 分组,
供 Claude 工作流逐家核验(同一公司只核一次,效率+一致性)。
产物: outputs/uniq_companies.json(公司→{buckets, cap, size, overseas_ds, brief, fit, primary_l1})
"""
import sys, io, json
from collections import Counter, defaultdict
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = Path(__file__).resolve().parent / "outputs"

pools = json.loads((OUT/"candidate_pools.json").read_text("utf-8"))

uniq = {}
for bkey, v in pools.items():
    for c in v["candidates"]:
        nm = c["name"].strip()
        if nm not in uniq:
            uniq[nm] = {"name": nm, "buckets": [], "l1s": [], "caps": Counter(),
                        "sizes": Counter(), "overseas_ds": [], "brief": c.get("brief", ""),
                        "fit": c.get("fit", "")}
        u = uniq[nm]
        u["buckets"].append(bkey)
        u["l1s"].append(v["l1"])
        if c.get("cap"): u["caps"][c["cap"]] += 1
        if c.get("size"): u["sizes"][c["size"]] += 1
        if c.get("overseas") and c["overseas"] not in u["overseas_ds"]:
            u["overseas_ds"].append(c["overseas"])

out = {}
for nm, u in uniq.items():
    primary_l1 = Counter(u["l1s"]).most_common(1)[0][0]
    out[nm] = {
        "name": nm,
        "primary_l1": primary_l1,
        "n_buckets": len(set(u["buckets"])),
        "buckets": sorted(set(u["buckets"])),
        "cap": (u["caps"].most_common(1)[0][0] if u["caps"] else "综合"),
        "size": (u["sizes"].most_common(1)[0][0] if u["sizes"] else "中"),
        "brief": u["brief"],
        "fit": u["fit"],
        "overseas_ds": u["overseas_ds"][:3],
    }

(OUT/"uniq_companies.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"唯一候选中企: {len(out)} 家(来自 {len(pools)} 桶)")
byl1 = Counter(v["primary_l1"] for v in out.values())
print("按主一级行业:", dict(byl1.most_common()))
multi = sum(1 for v in out.values() if v["n_buckets"] >= 3)
print(f"出现在≥3桶的跨域企业: {multi}")
print("出现最多的前15家:")
for nm, v in sorted(out.items(), key=lambda x: -x[1]["n_buckets"])[:15]:
    print(f"  {nm} ({v['n_buckets']}桶, {v['primary_l1']}, {v['cap']}{v['size']})")
