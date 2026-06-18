# -*- coding: utf-8 -*-
"""抽取高价值在投机会(含葡语原文与友好方式标记),供 LLM 评估真梗概/可承接性/时效。"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ctr = pd.read_parquet("analysis/report/outputs/cleaned/招标主表.parquet")
open_mask = ctr["时效状态"].isin(["还能投", "临近截止"])
d = ctr[open_mask].copy()
d["v"] = d["valor_brl_clean"].where(d["valor_brl_clean"] > 0)
d = d.dropna(subset=["v"]).nlargest(40, "v")
cols = ["PNCP编号", "主维度", "采购方式", "valor_brl_clean", "valor_cny_clean",
        "标的(原文)", "标的(中文梗概)", "采购机构", "州", "投标截止", "剩余天数",
        "价格登记", "长期机会"]
recs = []
for _, r in d.iterrows():
    rec = {c: r[c] for c in cols}
    rec["投标截止"] = str(rec["投标截止"])[:10]
    recs.append(rec)
Path("analysis/report/outputs/highvalue_raw.json").write_text(
    json.dumps(recs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print("Saved highvalue_raw.json:", len(recs), "records")
for r in recs[:5]:
    print(" ", r["主维度"], f"{r['valor_brl_clean']/1e6:.1f}M", r["剩余天数"], "天 |", str(r["标的(原文)"])[:60])
