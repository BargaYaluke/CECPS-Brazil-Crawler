# -*- coding: utf-8 -*-
"""数据清洗:基于 01 画像结论,处理确认错误/未披露金额/重复/类型,产出 cleaned 数据 + 清洗日志。

清洗规则(全部可审计,见 cleaning_log.json):
  招标主表:
    - is_value_error = 预估金额 > 1e10 (100亿 BRL)。仅命中 Santa Rita 75.2B(小镇公园改造,
      物理不可能,为次大值11倍)。州级铁路/医疗/工资代发 2~9B 属真实大额,保留。
    - valor_brl_clean: 错误置 NaN。valor_disclosed: 0 视为"未披露预算"(PNCP sigilo)→ NaN。
  已签合同:
    - is_value_error = 合同金额 > 2e10 (200亿)。仅命中 2.8万亿(≈巴西GDP25%,工期仅1天)。
    - 同样区分 0=未披露。flag 工期异常(到期<签订 或 工期=0天)。
  采购明细:
    - 项号 > 9999 视为不可靠(实为 PNCP 内部item-id,非序号)→ numero_item_reliable=False。
    - 单价/小计 0 → 未披露。
  年度采购计划:
    - 删除"整行完全重复"(缓存重灌产生,占61%);3键重复是合法子项,不删。
  机构画像:
    - 累计额 > 1e10 标记 is_value_error(Santa Rita 75.2B 由那条错误招标聚合而来),tier 不可信。
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

SRC = Path("data/exports/report.xlsx")
OUTD = Path("analysis/report/outputs/cleaned"); OUTD.mkdir(parents=True, exist_ok=True)
LOG = Path("analysis/report/outputs")

log: dict = {"rules": {}, "before": {}, "after": {}, "flagged_records": {}}

# ── 1. 招标主表 ──────────────────────────────────────────────
ctr = pd.read_excel(SRC, sheet_name="招标主表")
log["before"]["招标主表"] = len(ctr)
TH_CTR = 1e10
ctr["is_value_error"] = ctr["预估金额(BRL)"] > TH_CTR
ctr["valor_brl_clean"] = ctr["预估金额(BRL)"].where(~ctr["is_value_error"])
ctr["valor_cny_clean"] = ctr["预估金额(CNY)"].where(~ctr["is_value_error"])
ctr["valor_disclosed"] = ctr["valor_brl_clean"].where(ctr["valor_brl_clean"] > 0)  # 0=未披露
ctr["is_undisclosed"] = ctr["预估金额(BRL)"].fillna(0).eq(0)
# 日期
for col in ["发布日期", "投标截止"]:
    ctr[col] = pd.to_datetime(ctr[col], errors="coerce")
ctr["截止_常年"] = ctr["投标截止"].dt.year.ge(2099)  # 2099=无截止
# 维度
ctr["主维度"] = ctr["主维度"].fillna("未分类")
log["flagged_records"]["招标主表_value_error"] = ctr.loc[ctr["is_value_error"],
    ["PNCP编号", "预估金额(BRL)", "采购机构", "标的(原文)"]].astype(str).to_dict("records")
log["rules"]["招标主表"] = {
    "value_error_threshold_brl": TH_CTR,
    "n_value_error": int(ctr["is_value_error"].sum()),
    "n_undisclosed_zero": int(ctr["is_undisclosed"].sum()),
    "n_perpetual_deadline_2099": int(ctr["截止_常年"].sum()),
}
ctr.to_parquet(OUTD / "招标主表.parquet", index=False)
log["after"]["招标主表"] = len(ctr)

# ── 2. 已签合同 ──────────────────────────────────────────────
con = pd.read_excel(SRC, sheet_name="已签合同")
log["before"]["已签合同"] = len(con)
TH_CON = 2e10
con["is_value_error"] = con["合同金额(BRL)"] > TH_CON
con["valor_brl_clean"] = con["合同金额(BRL)"].where(~con["is_value_error"])
con["is_undisclosed"] = con["合同金额(BRL)"].fillna(0).eq(0)
for col in ["签订日", "到期日"]:
    con[col] = pd.to_datetime(con[col], errors="coerce")
con["工期天数"] = (con["到期日"] - con["签订日"]).dt.days
con["is_duration_suspect"] = con["工期天数"].le(0)
log["flagged_records"]["已签合同_value_error"] = con.loc[con["is_value_error"],
    ["合同编号", "合同金额(BRL)", "中标供应商", "采购机构", "工期天数"]].astype(str).to_dict("records")
log["rules"]["已签合同"] = {
    "value_error_threshold_brl": TH_CON,
    "n_value_error": int(con["is_value_error"].sum()),
    "n_undisclosed_zero": int(con["is_undisclosed"].sum()),
    "n_duration_suspect": int(con["is_duration_suspect"].sum()),
}
con.to_parquet(OUTD / "已签合同.parquet", index=False)
log["after"]["已签合同"] = len(con)

# ── 3. 采购明细 ──────────────────────────────────────────────
it = pd.read_excel(SRC, sheet_name="采购明细")
log["before"]["采购明细"] = len(it)
it["numero_item_reliable"] = it["项号"] <= 9999
it["单价_disclosed"] = it["单价(BRL)"].where(it["单价(BRL)"] > 0)
it["小计_disclosed"] = it["小计(BRL)"].where(it["小计(BRL)"] > 0)
log["rules"]["采购明细"] = {
    "n_item_no_unreliable": int((~it["numero_item_reliable"]).sum()),
    "n_unit_price_zero": int(it["单价(BRL)"].fillna(0).eq(0).sum()),
}
it.to_parquet(OUTD / "采购明细.parquet", index=False)
log["after"]["采购明细"] = len(it)

# ── 4. 年度采购计划:删完全重复行 ────────────────────────────
pca = pd.read_excel(SRC, sheet_name="年度采购计划")
log["before"]["年度采购计划"] = len(pca)
n_full_dup_drop = int(pca.duplicated(keep="first").sum())
pca_clean = pca.drop_duplicates(keep="first").reset_index(drop=True)
pca_clean["期望采购日"] = pd.to_datetime(pca_clean["期望采购日"], errors="coerce")
log["rules"]["年度采购计划"] = {
    "exact_full_row_duplicates_removed": n_full_dup_drop,
    "rows_kept": len(pca_clean),
    "dedup_basis": "全部9列完全相同(缓存重灌产物);保留首次出现",
}
pca_clean.to_parquet(OUTD / "年度采购计划.parquet", index=False)
log["after"]["年度采购计划"] = len(pca_clean)

# ── 5. 机构画像 ──────────────────────────────────────────────
org = pd.read_excel(SRC, sheet_name="机构画像")
log["before"]["机构画像"] = len(org)
org["is_value_error"] = org["近2年累计额(BRL)"] > 1e10
org["valor_2y_clean"] = org["近2年累计额(BRL)"].where(~org["is_value_error"])
org["last_active_date"] = pd.to_datetime(org["最近活跃日"], errors="coerce")
log["rules"]["机构画像"] = {
    "n_value_error": int(org["is_value_error"].sum()),
    "note": "tier 列对 value_error 机构不可信(由错误招标聚合)",
}
org.to_parquet(OUTD / "机构画像.parquet", index=False)
log["after"]["机构画像"] = len(org)

# ── 6. 小表原样保存(维度透视会在统计阶段重算,这里存亲外企/字段说明)──
for sh in ["维度透视", "亲外企机构", "字段说明"]:
    df = pd.read_excel(SRC, sheet_name=sh)
    df.to_parquet(OUTD / f"{sh}.parquet", index=False)
    log["before"][sh] = log["after"][sh] = len(df)

(LOG / "cleaning_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

# ── 打印小结 ──
print("="*60, "\n清洗小结(before -> after)")
for sh in log["before"]:
    print(f"  {sh}: {log['before'][sh]:>8,} -> {log['after'][sh]:>8,}")
print("\n规则命中:")
print(json.dumps(log["rules"], ensure_ascii=False, indent=2, default=str))
print("\n确认错误记录:")
for k, recs in log["flagged_records"].items():
    print(f"  [{k}] {len(recs)} 条")
    for r in recs:
        print("   ", {kk: (vv[:40] if isinstance(vv, str) else vv) for kk, vv in r.items()})
