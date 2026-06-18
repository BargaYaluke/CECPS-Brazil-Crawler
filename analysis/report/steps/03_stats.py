# -*- coding: utf-8 -*-
"""统计分析 + 数据挖掘 → analysis/report/outputs/stats.json(喂图表与洞察 agent)。

三大视角:市场全景 / 中企商机 / 竞品情报 + 数据挖掘(共现/交叉/聚类/文本/季节性)。
金额一律用清洗后列(valor_brl_clean / *_disclosed),已排除确认错误与未披露 0。
"""
from __future__ import annotations
import json, re, sqlite3
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

CL = Path("analysis/report/outputs/cleaned")
OUT = Path("analysis/report/outputs")

def J(o):
    """numpy→native 递归,供 json。"""
    if isinstance(o, dict):  return {str(k): J(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [J(x) for x in o]
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)):
        return None if (np.isnan(o) or np.isinf(o)) else round(float(o), 4)
    if isinstance(o, float):
        return None if (np.isnan(o) or np.isinf(o)) else round(o, 4)
    if isinstance(o, (pd.Timestamp,)): return o.strftime("%Y-%m-%d")
    return o

ctr = pd.read_parquet(CL / "招标主表.parquet")
con = pd.read_parquet(CL / "已签合同.parquet")
it  = pd.read_parquet(CL / "采购明细.parquet")
pca = pd.read_parquet(CL / "年度采购计划.parquet")
org = pd.read_parquet(CL / "机构画像.parquet")
foreign = pd.read_parquet(CL / "亲外企机构.parquet")

# 合同补国别(xlsx 未导出 codigo_pais_fornecedor,从 DB 取)
dbc = sqlite3.connect("data/procurement.db")
pais = pd.read_sql("select pncp_id as 合同编号, codigo_pais_fornecedor as pais from contratos", dbc)
dbc.close()
con = con.merge(pais, on="合同编号", how="left")

S: dict = {}

# ════════════ META ════════════
S["meta"] = {
    "采集窗口_发布": [ctr["发布日期"].min(), ctr["发布日期"].max()],
    "n_招标": len(ctr), "n_明细": len(it), "n_合同": len(con),
    "n_PCA": len(pca), "n_机构": len(org),
    "note": "金额=清洗后(已排除2笔确认错误+未披露0);PCA已去重",
}

# ════════════ 1. 市场全景 ════════════
def agg_value(df, by, valcol="valor_brl_clean", topn=None, disclosed_only=True):
    d = df.copy()
    v = d[valcol]
    if disclosed_only:
        v = v.where(v > 0)
    d = d.assign(_v=v)
    g = d.groupby(by, dropna=False).agg(
        n=("_v", "size"),
        n_disclosed=("_v", "count"),
        total=("_v", "sum"),
        median=("_v", "median"),
    ).reset_index().sort_values("n", ascending=False)
    if topn: g = g.head(topn)
    return g.to_dict("records")

mo = {}
v = ctr["valor_brl_clean"].where(ctr["valor_brl_clean"] > 0)
mo["totals"] = {
    "n_招标": len(ctr),
    "n_披露金额": int(v.count()),
    "n_未披露": int(ctr["is_undisclosed"].sum()),
    "总额_BRL": float(v.sum()),
    "总额_CNY": float(ctr["valor_cny_clean"].where(ctr["valor_cny_clean"] > 0).sum()),
    "中位数_BRL": float(v.median()),
    "均值_BRL": float(v.mean()),
    "p90_BRL": float(v.quantile(.90)), "p99_BRL": float(v.quantile(.99)),
}
mo["by_dimension"] = agg_value(ctr, "主维度")
mo["by_modality"]  = agg_value(ctr, "采购方式")
mo["by_region"]    = agg_value(ctr, "大区")
mo["by_uf"]        = agg_value(ctr, "州", topn=15)
mo["by_gdp_tier"]  = agg_value(ctr, "GDP分层")
mo["by_esfera"]    = agg_value(ctr, "级别")
# 金额分桶
bins=[0,1e4,1e5,1e6,1e7,1e8,1e10]; labels=["<1万","1万-10万","10万-100万","100万-1000万","1000万-1亿","1亿-100亿"]
buck = pd.cut(v.dropna(), bins=bins, labels=labels, right=True)
mo["value_buckets"] = {str(k): int(x) for k, x in buck.value_counts().reindex(labels).items()}
# 时间趋势:按发布日
ctr["发布日"] = ctr["发布日期"].dt.date
trend = ctr.groupby("发布日").agg(n=("PNCP编号","size"), total=("valor_brl_clean","sum")).reset_index()
mo["trend_by_pubdate"] = [{"日期": str(r["发布日"]), "n": int(r["n"]), "total": J(r["total"])} for _,r in trend.iterrows()]
# 投标截止月份(排除2099常年)
dl = ctr.loc[~ctr["截止_常年"], "投标截止"].dropna()
dlm = dl.dt.to_period("M").value_counts().sort_index()
mo["deadline_by_month"] = {str(k): int(x) for k, x in dlm.items()}
S["market_overview"] = mo

# ════════════ 2. 中企商机 ════════════
op = {}
op["deadline_status"] = ctr.groupby("时效状态").agg(
    n=("PNCP编号","size"), total=("valor_brl_clean","sum")).reset_index().sort_values("n",ascending=False).to_dict("records")
open_mask = ctr["时效状态"].isin(["还能投","临近截止"])
opn = ctr[open_mask]
op["open_now"] = {"n": int(open_mask.sum()),
    "total_BRL": float(opn["valor_brl_clean"].where(opn["valor_brl_clean"]>0).sum()),
    "n_临近截止": int((ctr["时效状态"]=="临近截止").sum())}
op["open_by_dimension"] = agg_value(opn, "主维度")
op["open_by_region"] = agg_value(opn, "大区")
op["open_by_modality"] = agg_value(opn, "采购方式")
# 高价值在投机会 top30
hv = opn.assign(_v=opn["valor_brl_clean"].where(opn["valor_brl_clean"]>0)).dropna(subset=["_v"]).nlargest(30,"_v")
op["high_value_open"] = hv[["PNCP编号","主维度","采购方式","valor_brl_clean","valor_cny_clean",
    "标的(中文梗概)","采购机构","州","投标截止","剩余天数"]].to_dict("records")
# 中企关注的友好采购方式 / 长期机会 / 价格登记
op["friendly_modes"] = {
    "电子拍卖_Pregao_Eletronico_在投": int(((ctr["采购方式"].str.contains("Eletrônic", na=False)) & open_mask).sum()),
    "长期机会_在投": int((ctr["长期机会"].eq("是") & open_mask).sum()),
    "价格登记SRP_在投": int((ctr["价格登记"].eq("是") & open_mask).sum()),
}
# PCA 商机管道(未来)
pca["valor"] = pd.to_numeric(pca["预估金额(BRL)"], errors="coerce")
op["pca_by_year"] = pca.groupby("计划年度").agg(n=("PCA编号","size"), total=("valor","sum")).reset_index().to_dict("records")
op["pca_top_categories"] = pca.groupby("物/服").agg(n=("PCA编号","size"), total=("valor","sum")).reset_index().sort_values("total",ascending=False).head(15).to_dict("records")
op["pca_top_superior"] = pca.groupby("上级分类").agg(n=("PCA编号","size"), total=("valor","sum")).reset_index().sort_values("total",ascending=False).head(20).to_dict("records")
pca_future = pca[pca["期望采购日"] >= pd.Timestamp("2026-06-01")]
op["pca_future_by_month"] = pca_future.assign(m=pca_future["期望采购日"].dt.to_period("M").astype(str)).groupby("m").agg(n=("PCA编号","size"), total=("valor","sum")).reset_index().head(24).to_dict("records")
op["pca_top_items"] = pca.nlargest(25,"valor")[["PCA编号","机构","采购标的","物/服","valor","期望采购日","上级分类"]].to_dict("records")
S["opportunities"] = op

# ════════════ 3. 竞品情报 ════════════
cp = {}
cv = con["valor_brl_clean"].where(con["valor_brl_clean"]>0)
con = con.assign(_cv=cv)
cp["totals"] = {"n_合同": len(con), "n_披露": int(cv.count()),
    "总额_BRL": float(cv.sum()), "中位_BRL": float(cv.median()), "均值_BRL": float(cv.mean())}
# 供应商集中度
sup = con.groupby("中标供应商").agg(n=("合同编号","size"), total=("_cv","sum")).reset_index()
sup_v = sup.sort_values("total",ascending=False)
cp["top_suppliers_by_value"] = sup_v.head(25).to_dict("records")
cp["top_suppliers_by_count"] = sup.sort_values("n",ascending=False).head(25).to_dict("records")
tot_v = sup["total"].sum()
shares = (sup_v["total"]/tot_v).fillna(0)
cp["concentration"] = {
    "n_unique_suppliers": int(sup["中标供应商"].nunique()),
    "top10_value_share_pct": float(100*shares.head(10).sum()),
    "top50_value_share_pct": float(100*shares.head(50).sum()),
    "HHI": float((shares**2).sum()*10000),  # 0-10000
}
cp["supplier_type"] = con.groupby("主体").agg(n=("合同编号","size"), total=("_cv","sum")).reset_index().to_dict("records")
cp["by_state"] = con.groupby("州").agg(n=("合同编号","size"), total=("_cv","sum")).reset_index().sort_values("total",ascending=False).head(15).to_dict("records")
# 外企
con["is_foreign"] = con["pais"].notna() & (con["pais"]!="BRA")
fc = con[con["is_foreign"]]
cp["foreign"] = {
    "n_foreign_contracts": int(con["is_foreign"].sum()),
    "pct_of_all": float(100*con["is_foreign"].mean()),
    "foreign_value_BRL": float(fc["_cv"].sum()),
    "by_country": fc.groupby("pais").agg(n=("合同编号","size"), total=("_cv","sum")).reset_index().to_dict("records"),
    "foreign_suppliers": fc[["中标供应商","pais","valor_brl_clean","采购机构"]].to_dict("records"),
}
cp["foreign_friendly_orgs"] = foreign.to_dict("records")
# 合同→招标维度 join(看各维度谁在中标 / 中标金额)
j = con.merge(ctr[["PNCP编号","主维度"]].rename(columns={"PNCP编号":"对应招标"}), on="对应招标", how="left")
cp["join_match_rate_pct"] = float(100*j["主维度"].notna().mean())
cp["contracts_by_dimension"] = j.dropna(subset=["主维度"]).groupby("主维度").agg(n=("合同编号","size"), total=("_cv","sum")).reset_index().sort_values("total",ascending=False).to_dict("records")
S["competition"] = cp

# ════════════ 4. 数据挖掘 ════════════
dm = {}
# 4.1 维度共现(主+次)
cooc = Counter()
for _, r in ctr.iterrows():
    p = r["主维度"]; sec = r["次维度"]
    if pd.notna(sec) and str(sec) not in ("None","nan",""):
        for s in str(sec).split(","):
            s=s.strip()
            if s and s!=p: cooc[tuple(sorted([p,s]))]+=1
dm["dimension_cooccurrence"] = [{"维度对": f"{a} + {b}", "n": n} for (a,b),n in cooc.most_common(15)]
# 4.2 采购方式 × 维度 交叉
ct = pd.crosstab(ctr["采购方式"], ctr["主维度"])
dm["modality_x_dimension"] = {"index": list(ct.index), "columns": list(ct.columns), "data": ct.values.tolist()}
# 4.3 机构买方聚类(纯 numpy KMeans;本机 sklearn 编译 DLL 被应用控制策略拦截)
def kmeans_np(X, k=4, iters=100, seed=42):
    rng = np.random.default_rng(seed)
    cent = X[rng.choice(len(X), k, replace=False)]
    for _ in range(iters):
        d = ((X[:,None,:]-cent[None,:,:])**2).sum(2)
        lab = d.argmin(1)
        new = np.array([X[lab==j].mean(0) if (lab==j).any() else cent[j] for j in range(k)])
        if np.allclose(new, cent): break
        cent = new
    return lab
og = org[org["valor_2y_clean"].notna() & (org["valor_2y_clean"]>0) & (org["近2年招标数"]>0)].copy()
og["log_val"] = np.log10(og["valor_2y_clean"]+1)
og["log_n"] = np.log10(og["近2年招标数"]+1)
og["avg_val"] = og["valor_2y_clean"]/og["近2年招标数"]
og["log_avg"] = np.log10(og["avg_val"]+1)
Xraw = og[["log_val","log_n","log_avg"]].to_numpy(dtype=float)
X = (Xraw - Xraw.mean(0)) / Xraw.std(0)  # 标准化
og["cluster"] = kmeans_np(X, k=4)
clus = og.groupby("cluster").agg(
    n_orgs=("机构CNPJ","size"),
    median_bids=("近2年招标数","median"),
    median_total=("valor_2y_clean","median"),
    median_avg=("avg_val","median"),
).reset_index()
dm["org_clusters"] = clus.to_dict("records")
dm["org_cluster_examples"] = {int(c): og[og["cluster"]==c].nlargest(3,"valor_2y_clean")["机构名称"].tolist() for c in sorted(og["cluster"].unique())}
# 4.4 文本挖掘:各维度 objeto 高频词(葡语,去停用词)
STOP = set("de da do dos das e a o os as para com em no na nos nas um uma por que ao aos referente prestacao prestação servico serviço serviços servicos empresa especializada contratacao contratação aquisicao aquisição municipio município secretaria saude saúde publica pública para fins futura eventual atender bem objeto registro precos preços lote portal compras publicas públicas o.a do(a)".split())
def top_terms(texts, k=15):
    c = Counter()
    for t in texts.dropna():
        for w in re.findall(r"[a-zà-úA-ZÀ-Ú]{4,}", str(t).lower()):
            if w not in STOP: c[w]+=1
    return [{"词": w, "n": n} for w,n in c.most_common(k)]
dm["keywords_by_dimension"] = {d: top_terms(ctr.loc[ctr["主维度"]==d,"标的(原文)"]) for d in ctr["主维度"].unique() if d!="未分类"}
# 4.5 季节性:发布按星期 / 维度置信度分布
dm["pub_by_weekday"] = {str(k): int(v) for k,v in ctr["发布日期"].dt.dayofweek.value_counts().sort_index().items()}
dm["confidence_dist"] = {"已分类均值": float(ctr.loc[ctr["主维度"]!="未分类","维度置信度"].mean()),
    "未分类占比_pct": float(100*ctr["主维度"].eq("未分类").mean())}
S["data_mining"] = dm

OUT.joinpath("stats.json").write_text(json.dumps(J(S), ensure_ascii=False, indent=2), encoding="utf-8")
print("Saved analysis/report/outputs/stats.json")
print("\n关键数字:")
print(f"  招标 {mo['totals']['n_招标']:,} 条,披露金额 {mo['totals']['n_披露金额']:,} 条,总额 {mo['totals']['总额_BRL']/1e9:.2f}B BRL,中位 {mo['totals']['中位数_BRL']:,.0f}")
print(f"  在投机会 {op['open_now']['n']:,} 条(临近截止 {op['open_now']['n_临近截止']:,})")
print(f"  合同 {cp['totals']['n_合同']:,},供应商 {cp['concentration']['n_unique_suppliers']:,},Top10份额 {cp['concentration']['top10_value_share_pct']:.1f}%,HHI {cp['concentration']['HHI']:.0f}")
print(f"  外企合同 {cp['foreign']['n_foreign_contracts']} 笔({cp['foreign']['pct_of_all']:.3f}%)")
print(f"  合同→维度 join 命中率 {cp['join_match_rate_pct']:.1f}%")
print(f"  PCA 去重后 {len(pca):,};机构聚类 4 群")
