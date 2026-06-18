# -*- coding: utf-8 -*-
"""Step4b-prep: 把 852 家唯一候选按 主一级行业 分组、切块(≤12家/块),
写 outputs/verify_in/g<NN>.json 供 Claude 工作流逐家核验。"""
import sys, io, json
from collections import defaultdict
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = Path(__file__).resolve().parent / "outputs"
VIN = OUT / "verify_in"
VIN.mkdir(exist_ok=True)
for f in VIN.glob("*.json"):
    f.unlink()

uniq = json.loads((OUT/"uniq_companies.json").read_text("utf-8"))

# 二级领域上下文(每家公司出现在哪些 l2)
def l2s_of(v):
    return sorted({b.split("|", 1)[1] for b in v["buckets"]})

by_l1 = defaultdict(list)
for nm, v in uniq.items():
    by_l1[v["primary_l1"]].append(v)

CHUNK = 12
batches = []
for l1, comps in by_l1.items():
    comps.sort(key=lambda x: -x["n_buckets"])
    for i in range(0, len(comps), CHUNK):
        batches.append((l1, comps[i:i+CHUNK]))

manifest = []
for bi, (l1, comps) in enumerate(batches):
    gid = f"g{bi:03d}"
    payload = {
        "batch_id": gid,
        "primary_l1": l1,
        "companies": [{
            "name": c["name"],
            "ds_brief": c["brief"],
            "ds_cap": c["cap"],
            "ds_size": c["size"],
            "ds_overseas": c["overseas_ds"],
            "domains": l2s_of(c),
        } for c in comps],
    }
    (VIN/f"{gid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest.append({"batch_id": gid, "primary_l1": l1, "n": len(comps)})

(OUT/"verify_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"写出 {len(batches)} 个核验批 → outputs/verify_in/  (共 {len(uniq)} 家, ≤{CHUNK}/批)")
print("批数按一级:", {l1: sum(1 for m in manifest if m['primary_l1']==l1) for l1 in by_l1})
