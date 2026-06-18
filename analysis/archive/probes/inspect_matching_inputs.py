# -*- coding: utf-8 -*-
"""Inspect the structure of the company tables, tender report, and any prior matching output."""
import sys
import io
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 240)
pd.set_option("display.max_colwidth", 60)

FILES = {
    "company_main": "data/exports/中葡企业机构数据库_企业登记表.xlsx",
    "company_order": "data/exports/中葡企业机构数据库_企业登记表_六大行业顺序.xlsx",
    "prior_match": "data/exports/中葡企业_巴西竞标匹配表.xlsx",
    "report": "data/exports/report.xlsx",
}

for key, path in FILES.items():
    print("=" * 100)
    print(f"### {key}  ->  {path}")
    print("=" * 100)
    try:
        xls = pd.ExcelFile(path)
    except Exception as e:
        print(f"  !! cannot open: {e}")
        continue
    print(f"  sheets: {xls.sheet_names}")
    for sh in xls.sheet_names:
        try:
            df = pd.read_excel(path, sheet_name=sh, nrows=200)
        except Exception as e:
            print(f"  -- sheet {sh}: read error {e}")
            continue
        # full row count
        try:
            full = pd.read_excel(path, sheet_name=sh, usecols=[0])
            nrows = len(full)
        except Exception:
            nrows = "?"
        print(f"\n  --- sheet: {sh!r}  | rows(full)={nrows} | cols={len(df.columns)}")
        print(f"      columns: {list(df.columns)}")
        print("      dtypes:")
        for c in df.columns:
            print(f"        - {c!r}: {df[c].dtype}, non-null(first200)={df[c].notna().sum()}")
        print("      head(3):")
        print(df.head(3).to_string())
