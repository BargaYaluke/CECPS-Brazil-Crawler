# -*- coding: utf-8 -*-
"""Step6c: 合并各校验智能体写出的 verdicts/<cid>.json -> verdicts.json,并对总表应用裁决。"""
import sys, io, json, glob, os
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

vdir = OUT/"verdicts"
merged = {}          # "cid|pncp" -> {结论, 说明}
files = sorted(glob.glob(str(vdir/"*.json")))
n_files, n_v = 0, 0
for f in files:
    try:
        d = json.loads(open(f, encoding="utf-8").read())
    except Exception as e:
        print("  跳过(解析失败):", os.path.basename(f), e); continue
    cid = d.get("cid") or os.path.splitext(os.path.basename(f))[0]
    for v in d.get("verdicts", []):
        pncp = v.get("pncp")
        if not pncp:
            continue
        merged[f"{cid}|{pncp}"] = {"结论": v.get("结论",""), "说明": v.get("说明","")}
        n_v += 1
    n_files += 1

(OUT/"verdicts.json").write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")

# 统计 + 对 matches 应用(剔除→标记;降级→保留但标注)
from collections import Counter
cnt = Counter(v["结论"] for v in merged.values())
print(f"裁决文件 {n_files}/41 | 裁决条目 {n_v}")
print("裁决分布:", dict(cnt))

# 应用到 matches: 给被剔除/降级的行打标(不物理删除,保留可追溯)
matches = json.loads((OUT/"matches.json").read_text("utf-8"))
applied = 0
for r in matches:
    v = merged.get(f"{r['cid']}|{r['PNCP编号']}")
    if v:
        applied += 1
print(f"总表 {len(matches)} 行中,有校验结论的 {applied} 行(高/中重要度本轮可竞标)")
