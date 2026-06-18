# -*- coding: utf-8 -*-
"""Ingest(在巴中企数据集): data/inputs/中国-巴西合作企业清单.xlsx -> company_master(.json/.csv)。

这批是【总部在中国、已在巴西设实体】的企业,与 step1 的 437 家是不同数据集、不同 schema。
本脚本把新表列映射成与 step1 输出一致的 company_master 字段,供下游 step3/10/11/12/13、step2/4/5/8/9 复用。
关键语义:全部 本地化状态=已在巴西、可匹配=True(不走 step1 的"剔除巴西本土"逻辑——它们是中企不是巴西本土企业)。

隔离:务必设 MATCH_OUT 指向独立 outputs 目录(如 outputs_partners),避免覆盖 437 家的产物与人工裁决。
"""
import io
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from match_lib import ROOT, OUT, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = ROOT / "data/inputs/中国-巴西合作企业清单.xlsx"
df = pd.read_excel(SRC, sheet_name="中国-巴西合作企业清单", dtype=str)
print(f"原始: {len(df)} 行")


def s(x):
    if x is None or (isinstance(x, float)) or pd.isna(x):
        return None
    v = str(x).strip()
    return v or None


# 主营方向(中心六大)可能多值/非标 -> 归一到六大行业(取首个主行业 + 别名对齐)
_IND_ALIAS = {"跨境电商": "跨境商贸", "医疗健康": "医疗医药"}


def to_industry(raw):
    v = s(raw)
    if not v:
        return None
    first = v.replace("，", ",").split(",")[0].strip()
    first = _IND_ALIAS.get(first, first)
    return norm_industry(first)


# 业务强度等级 -> 重要程度 / imp_rank(A类已有实体=最高优先,B=中,C=低)
def imp_of(level):
    t = (s(level) or "").upper()
    if t.startswith("A"):
        return "1高", 1
    if t.startswith("B"):
        return "2中", 2
    if t.startswith("C"):
        return "3低", 3
    return "无标识(最低)", 4


recs = []
for _, r in df.iterrows():
    name_zh = s(r.get("企业中文名"))
    if not name_zh:
        continue
    imp_label, imp_rank = imp_of(r.get("业务强度等级"))
    # 主营业务:行业细分 + 主营产品/服务 拼成一段供画像抽取产品线
    biz = " | ".join([v for v in (s(r.get("行业细分")), s(r.get("主营产品/服务"))) if v])
    recs.append({
        "cid": None,  # 排序后再编号
        "公司简称": name_zh,
        "公司全称": s(r.get("企业外文名")) or name_zh,
        "境内/境外": "境外(已在巴西)",
        "所在国别": "中国",                       # 中资企业(总部在华),不触发"剔除巴西本土"
        "六大行业": s(r.get("主营方向(中心六大)")),
        "公司重要程度": imp_label,
        "主营业务": biz,
        "十项服务": s(r.get("合作需求关键词")),
        "业务状态": s(r.get("实体类型")),
        "企业拜访表": s(r.get("近两年关键事件")),  # step3 当"近期动态"用
        "对接渠道细分": s(r.get("公开联系入口")),
        "对接人": s(r.get("海外业务负责人")),
        "计数": None,
        "imp_rank": imp_rank,
        "重要程度": imp_label,
        "行业": to_industry(r.get("主营方向(中心六大)")),
        "本地化状态": "已在巴西",                 # 全部已有巴西实体 -> 可直投
        "出海需求": s(r.get("合作需求关键词")),
        "巴西企业": False,                        # 中企,非巴西本土,不剔除
        "可匹配": True,
        # 额外留档(不被下游强依赖,便于追溯)
        "_母公司": s(r.get("母公司/集团")),
        "_总部": s(r.get("总部所在地")),
        "_在地": s(r.get("在地城市/州/园区")),
        "_实体类型": s(r.get("实体类型")),
        "_合作初判": s(r.get("合作可能性初判")),
        "_公司规模": s(r.get("公司规模")),
    })

# 稳定排序 + 编号(与 step1 一致:重要程度 -> 行业 -> 名称)
recs.sort(key=lambda x: (x["imp_rank"], x["行业"] or "zzz", x["公司简称"]))
for i, c in enumerate(recs):
    c["cid"] = "C%03d" % i

OUT.mkdir(parents=True, exist_ok=True)
pd.DataFrame(recs).to_csv(OUT / "company_master.csv", index=False, encoding="utf-8-sig")
(OUT / "company_master.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"输出 -> {OUT}")
print(f"企业数: {len(recs)}")
print("\n重要程度分布:")
print(pd.Series([c["重要程度"] for c in recs]).value_counts().to_string())
print("\n行业分布:")
print(pd.Series([c["行业"] for c in recs]).value_counts(dropna=False).to_string())
miss = [c["公司简称"] for c in recs if not (c["主营业务"] and c["行业"])]
print(f"\n主营/行业缺失(会落到 step3b 推断)的: {len(miss)} {miss[:10]}")
print("\n样例(前5):")
for c in recs[:5]:
    print(f'  {c["cid"]} {c["公司简称"]} | {c["行业"]} | {c["重要程度"]} | {(c["主营业务"] or "")[:40]}')
