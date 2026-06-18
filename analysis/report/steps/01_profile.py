# -*- coding: utf-8 -*-
"""深度数据画像:读 report.xlsx 全部 sheet,输出结构/缺失/分布/异常/重复。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

SRC = Path("data/exports/report.xlsx")
OUT = Path("analysis/report/outputs")
OUT.mkdir(parents=True, exist_ok=True)

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)

SHEETS = ["招标主表", "采购明细", "已签合同", "年度采购计划", "机构画像",
          "维度透视", "亲外企机构", "过滤审计", "字段说明"]

# 各 sheet 的数值列(用于分布/异常检测)
NUMERIC = {
    "招标主表": ["预估金额(BRL)", "预估金额(CNY)", "维度置信度", "剩余天数"],
    "采购明细": ["项号", "单价(BRL)", "小计(BRL)"],
    "已签合同": ["合同金额(BRL)"],
    "年度采购计划": ["计划年度", "预估金额(BRL)"],
    "机构画像": ["近2年招标数", "近2年累计额(BRL)"],
}
# 各 sheet 的关键去重键
DEDUP_KEYS = {
    "招标主表": ["PNCP编号"],
    "已签合同": ["合同编号"],
    "采购明细": ["PNCP编号", "项号"],
    "年度采购计划": ["PCA编号", "采购标的", "期望采购日"],
    "机构画像": ["机构CNPJ"],
}

report = {}

def pctiles(s: pd.Series) -> dict:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return {}
    qs = [0, .01, .05, .25, .5, .75, .95, .99, 1.0]
    out = {f"p{int(q*100)}": float(s.quantile(q)) for q in qs}
    out.update(mean=float(s.mean()), std=float(s.std()), count=int(s.count()),
               n_zero=int((s == 0).sum()), n_neg=int((s < 0).sum()))
    # IQR 异常上界
    q1, q3 = s.quantile(.25), s.quantile(.75)
    iqr = q3 - q1
    hi = q3 + 3 * iqr
    out["iqr_upper_3x"] = float(hi)
    out["n_outliers_hi"] = int((s > hi).sum())
    return out

for sh in SHEETS:
    df = pd.read_excel(SRC, sheet_name=sh)
    info = {
        "n_rows": int(len(df)),
        "n_cols": int(df.shape[1]),
        "columns": list(df.columns),
        "null_rate": {c: round(float(df[c].isna().mean()), 4) for c in df.columns},
        "n_unique": {c: int(df[c].nunique(dropna=True)) for c in df.columns},
    }
    # 重复
    if sh in DEDUP_KEYS:
        keys = DEDUP_KEYS[sh]
        dup = int(df.duplicated(subset=keys, keep=False).sum())
        info["dup_on_keys"] = {"keys": keys, "n_dup_rows": dup}
    # 数值分布
    if sh in NUMERIC:
        info["numeric"] = {c: pctiles(df[c]) for c in NUMERIC[sh] if c in df.columns}
    # 类别列 top 值(挑代表性列)
    cat_cols = {
        "招标主表": ["采购方式", "主维度", "州", "大区", "GDP分层", "时效状态", "级别", "状态"],
        "已签合同": ["主体", "州"],
        "年度采购计划": ["计划年度", "物/服"],
        "机构画像": ["级别", "机构分层", "州"],
    }.get(sh, [])
    if cat_cols:
        info["top_values"] = {
            c: {str(k): int(v) for k, v in df[c].value_counts(dropna=False).head(12).items()}
            for c in cat_cols if c in df.columns
        }
    report[sh] = info
    print(f"[{sh}] rows={info['n_rows']} cols={info['n_cols']}")

(OUT / "profile.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("\nSaved analysis/report/outputs/profile.json")

# ── 打印关键发现 ──
print("\n===== 数值异常速览 =====")
for sh, info in report.items():
    for c, st in info.get("numeric", {}).items():
        if not st:
            continue
        print(f"{sh} / {c}: max={st['p100']:,.2f}  p99={st['p99']:,.2f}  "
              f"p50={st['p50']:,.2f}  零={st['n_zero']} 负={st['n_neg']} "
              f"高异常({st['n_outliers_hi']})>{st['iqr_upper_3x']:,.0f}")

print("\n===== 缺失率 > 20% 的列 =====")
for sh, info in report.items():
    for c, r in info["null_rate"].items():
        if r > 0.20:
            print(f"{sh} / {c}: {r:.1%}")

print("\n===== 重复 =====")
for sh, info in report.items():
    d = info.get("dup_on_keys")
    if d and d["n_dup_rows"]:
        print(f"{sh}: {d['n_dup_rows']} 行在 {d['keys']} 上重复")
