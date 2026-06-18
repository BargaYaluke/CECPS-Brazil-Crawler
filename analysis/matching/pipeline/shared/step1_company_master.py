# -*- coding: utf-8 -*-
"""Step1: 合并 企业登记表 + 重要程度表 -> 唯一企业主表(清洗 + 规范化)。
   v3 变更: ① 并入出海需求表『出海诉求/跟进记录』为 出海需求 字段(进画像/评分/校验);
            ② 剔除巴西本土企业(所在国别=巴西) —— 业务是帮企业出海巴西,巴西企业不在服务范围。"""
import sys, io, json
import pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import ROOT, OUT, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

MAIN  = ROOT / "data/inputs/企业登记表.xlsx"
ORDER = ROOT / "data/inputs/重要程度表.xlsx"
VISIT = ROOT / "data/inputs/出海需求表.xlsx"

cm = pd.read_excel(MAIN, sheet_name="企业登记表")
co = pd.read_excel(ORDER, sheet_name="企业登记表")

# 两份文件同一批数据(不同排序)。纵向拼接后按 公司简称+公司全称 去重并字段合并(取非空)。
df = pd.concat([cm, co], ignore_index=True)

# 规范化键
def s(x):
    return None if pd.isna(x) else str(x).strip()

df["公司简称"] = df["公司简称"].map(s)
df["公司全称"] = df["公司全称"].map(s)
# 唯一键:优先 公司全称,缺失用 公司简称
df["_key"] = df["公司全称"].fillna(df["公司简称"])

KEEP = ["公司简称","公司全称","境内/境外","所在国别","六大行业","公司重要程度","主营业务",
        "十项服务","业务状态","企业拜访表","对接渠道细分","对接人","计数"]

def coalesce(group: pd.DataFrame) -> pd.Series:
    out = {}
    for c in KEEP:
        vals = [v for v in group[c].tolist() if not (pd.isna(v) or (isinstance(v,str) and not v.strip()))]
        # 取最长的非空(信息最全)
        out[c] = max((str(v) for v in vals), key=len) if vals else None
    return pd.Series(out)

master = df.groupby("_key", dropna=True).apply(coalesce).reset_index(drop=True)

# 规范化 重要程度 -> imp_rank (1高=1 ... 无=4) + 标签
IMP_MAP = {"1高":1, "2中":2, "3低":3}
def imp_rank(x):
    return IMP_MAP.get(str(x).strip(), 4) if x and not pd.isna(x) else 4
master["imp_rank"] = master["公司重要程度"].map(imp_rank)
master["重要程度"] = master["公司重要程度"].fillna("无标识(最低)")

# 行业规范化
master["行业"] = master["六大行业"].map(norm_industry)

# 本地化状态:所在国别==巴西 -> 已在巴西(可直投);葡语国家/其他境外 -> 部分;中国/其他 -> 需本地化
def loc_status(row):
    pais = str(row.get("所在国别") or "").strip()
    if pais == "巴西":
        return "已在巴西"
    if pais in ("葡萄牙",):
        return "葡语区(较易)"
    return "需本地化"
master["本地化状态"] = master.apply(loc_status, axis=1)

# 出海需求:拜访表『出海诉求/跟进记录』按企业名称去重合并(同 add_overseas_need 口径),
# 以 公司简称/公司全称 双键连接
v = pd.read_excel(VISIT, dtype=str)
v["企业名称"] = v["企业名称"].astype(str).str.strip()
def _clean(x):
    x = str(x).strip()
    return "" if x.lower() in ("", "nan", "none") else x
need = {}
for name, grp in v.groupby("企业名称"):
    vals = []
    for x in grp["出海诉求/跟进记录"]:
        c = _clean(x)
        if c and c not in vals:
            vals.append(c)
    if vals:
        need[name] = "\n\n".join(vals)
def lookup_need(row):
    for k in (row.get("公司简称"), row.get("公司全称")):
        if k and str(k).strip() in need:
            return need[str(k).strip()]
    return None
master["出海需求"] = master.apply(lookup_need, axis=1)

# 给每家企业一个稳定 id
master = master.sort_values(["imp_rank","行业","公司简称"], na_position="last").reset_index(drop=True)
master.insert(0, "cid", ["C%03d" % i for i in range(len(master))])

# 巴西本土企业剔除(出海服务对象之外):不参与匹配
master["巴西企业"] = master["所在国别"].map(lambda x: str(x).strip() == "巴西")

# 全部非巴西企业均参与匹配(主营/行业缺失的由 step3b 据公司名推断画像)
master["可匹配"] = ~master["巴西企业"]

print("企业主表行数:", len(master))
print("\n重要程度分布:")
print(master["重要程度"].value_counts(dropna=False).to_string())
print("\n行业分布:")
print(master["行业"].value_counts(dropna=False).to_string())
print("\n本地化状态:")
print(master["本地化状态"].value_counts(dropna=False).to_string())
print("\n巴西本土企业(剔除不匹配):", int(master["巴西企业"].sum()))
print(master.loc[master["巴西企业"], "公司简称"].tolist())
print("\n出海需求 非空:", int(master["出海需求"].notna().sum()), "/", len(master))
print("\n可匹配(非巴西企业):", int(master["可匹配"].sum()), " / 剔除:", int((~master["可匹配"]).sum()))

# 保存
master.to_csv(OUT/"company_master.csv", index=False, encoding="utf-8-sig")
recs = master.where(pd.notna(master), None).to_dict(orient="records")
(OUT/"company_master.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
print("\n已保存:", OUT/"company_master.csv", "和 .json")
print("\n样例(前5):")
print(master[["cid","公司简称","重要程度","行业","本地化状态","可匹配"]].head(5).to_string())
