# -*- coding: utf-8 -*-
"""生成报告图表(matplotlib, 中文字体 Microsoft YaHei),输出 analysis/report/outputs/charts/*.png。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.ticker import FuncFormatter

# ── 中文字体 ──
FONT = "C:/Windows/Fonts/msyh.ttc"
fm.fontManager.addfont(FONT)
_name = fm.FontProperties(fname=FONT).get_name()
plt.rcParams["font.sans-serif"] = [_name, "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 130
plt.rcParams["savefig.bbox"] = "tight"
plt.rcParams["savefig.pad_inches"] = 0.18
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.25
plt.rcParams["figure.constrained_layout.use"] = True  # 自动避让,防标签遮盖重叠

# 配色
BLUE="#2F5597"; LBLUE="#8FAADC"; ORANGE="#ED7D31"; GREEN="#548235"; GREY="#A6A6A6"
RED="#C00000"; PURPLE="#7030A0"
PALETTE=[BLUE,ORANGE,GREEN,LBLUE,PURPLE,RED,GREY,"#FFC000"]

CL = Path("analysis/report/outputs/cleaned")
CH = Path("analysis/report/outputs/charts"); CH.mkdir(parents=True, exist_ok=True)
S = json.load(open("analysis/report/outputs/stats.json", encoding="utf-8"))

def save(fig, name):
    fig.savefig(CH / name); plt.close(fig); print("  ✓", name)

def yi_fmt(x, _=None):  # 金额格式化(亿/万)
    if abs(x) >= 1e8: return f"{x/1e8:.1f}亿"
    if abs(x) >= 1e4: return f"{x/1e4:.0f}万"
    return f"{x:.0f}"

ctr = pd.read_parquet(CL/"招标主表.parquet")
con = pd.read_parquet(CL/"已签合同.parquet")
org = pd.read_parquet(CL/"机构画像.parquet")

# ════ 1. 维度:招标数 + 金额(双轴)════
d = [r for r in S["market_overview"]["by_dimension"]]
d = sorted(d, key=lambda r: r["n"], reverse=True)
labels=[r["主维度"] for r in d]; ns=[r["n"] for r in d]; vals=[(r["total"] or 0)/1e8 for r in d]
fig,ax1=plt.subplots(figsize=(9,4.8))
x=np.arange(len(labels)); w=0.4
b1=ax1.bar(x-w/2, ns, w, color=BLUE, label="招标数")
ax1.set_ylabel("招标数", color=BLUE); ax1.tick_params(axis='y',labelcolor=BLUE)
ax2=ax1.twinx(); ax2.grid(False)
b2=ax2.bar(x+w/2, vals, w, color=ORANGE, label="披露金额(亿BRL)")
ax2.set_ylabel("披露金额(亿 BRL)", color=ORANGE); ax2.tick_params(axis='y',labelcolor=ORANGE)
ax1.set_xticks(x); ax1.set_xticklabels(labels, rotation=20, ha="right")
ax1.set_title("六大维度:招标数 vs 披露金额", fontsize=13, weight="bold")
ax1.set_ylim(0, max(ns)*1.15)
for i,v in enumerate(ns): ax1.text(i-w/2, v, str(v), ha="center", va="bottom", fontsize=8)
ax1.legend(handles=[b1, b2], labels=["招标数(左轴)", "披露金额·亿BRL(右轴)"], loc="upper right", fontsize=9)
save(fig,"01_dimension.png")

# ════ 2. 地区:大区 ════
d=sorted(S["market_overview"]["by_region"], key=lambda r:r["n"], reverse=True)
labels=[r["大区"] for r in d]; ns=[r["n"] for r in d]; vals=[(r["total"] or 0)/1e8 for r in d]
fig,ax=plt.subplots(figsize=(8,4.5))
x=np.arange(len(labels)); w=0.4
bb1=ax.bar(x-w/2,ns,w,color=BLUE,label="招标数(左轴)")
ax2=ax.twinx(); ax2.grid(False); bb2=ax2.bar(x+w/2,vals,w,color=ORANGE,label="披露金额·亿BRL(右轴)")
ax.set_xticks(x); ax.set_xticklabels(labels)
ax.set_ylabel("招标数",color=BLUE); ax2.set_ylabel("披露金额(亿BRL)",color=ORANGE)
ax.set_title("五大区:招标数 vs 披露金额", fontsize=13, weight="bold")
ax.legend(handles=[bb1, bb2], loc="upper center", fontsize=9)
save(fig,"02_region.png")

# ════ 3. 金额分桶 ════
vb=S["market_overview"]["value_buckets"]
fig,ax=plt.subplots(figsize=(8,4.3))
bars=ax.bar(list(vb.keys()), list(vb.values()), color=BLUE)
ax.bar_label(bars, fmt="%d", fontsize=9)
ax.set_title("招标预估金额分布(披露金额,共 %d 条)"%sum(vb.values()), fontsize=13, weight="bold")
ax.set_ylabel("招标数"); plt.xticks(rotation=15)
save(fig,"03_value_dist.png")

# ════ 4. 时效状态 donut ════
ds=S["opportunities"]["deadline_status"]
order=["还能投","临近截止","已过期","无截止","未知"]
ds=sorted(ds, key=lambda r: order.index(r["时效状态"]) if r["时效状态"] in order else 9)
labels=[r["时效状态"] for r in ds]; sizes=[r["n"] for r in ds]
cols={"还能投":GREEN,"临近截止":ORANGE,"已过期":GREY,"无截止":LBLUE,"未知":"#D9D9D9"}
fig,ax=plt.subplots(figsize=(6.5,5))
wedges,_,_=ax.pie(sizes, labels=labels, autopct=lambda p:f"{p:.1f}%\n({int(round(p*sum(sizes)/100))})",
    colors=[cols.get(l,GREY) for l in labels], startangle=90, pctdistance=0.78,
    wedgeprops=dict(width=0.42, edgecolor="w"))
ax.set_title("招标时效状态分布(可投标窗口)", fontsize=13, weight="bold")
save(fig,"04_deadline.png")

# ════ 5. 采购方式 top8 ════
d=sorted(S["market_overview"]["by_modality"], key=lambda r:r["n"], reverse=True)[:8]
labels=[r["采购方式"] for r in d]; ns=[r["n"] for r in d]
fig,ax=plt.subplots(figsize=(8.5,4.5))
bars=ax.barh(labels[::-1], ns[::-1], color=BLUE)
ax.bar_label(bars, fmt="%d", fontsize=9, padding=2)
ax.set_title("采购方式分布(Top 8)", fontsize=13, weight="bold"); ax.set_xlabel("招标数")
save(fig,"05_modality.png")

# ════ 6. 级别 pie ════
em={"M":"市级","E":"州级","F":"联邦","N":"不适用","D":"其他"}
d=sorted(S["market_overview"]["by_esfera"], key=lambda r:r["n"], reverse=True)
labels=[em.get(r["级别"],str(r["级别"])) for r in d]; sizes=[r["n"] for r in d]
fig,ax=plt.subplots(figsize=(6,5))
ax.pie(sizes, labels=labels, autopct="%1.1f%%", colors=PALETTE, startangle=90,
    wedgeprops=dict(edgecolor="w"))
ax.set_title("采购机构行政级别分布", fontsize=13, weight="bold")
save(fig,"06_esfera.png")

# ════ 7. 发布趋势(日)════
tr=pd.DataFrame(S["market_overview"]["trend_by_pubdate"])
tr["日期"]=pd.to_datetime(tr["日期"]); tr=tr.sort_values("日期")
fig,ax=plt.subplots(figsize=(9.5,4))
ax.plot(tr["日期"], tr["n"], marker="o", color=BLUE, lw=1.8)
ax.fill_between(tr["日期"], tr["n"], color=LBLUE, alpha=0.3)
ax.set_title("招标发布数量趋势(按日)", fontsize=13, weight="bold"); ax.set_ylabel("招标数")
fig.autofmt_xdate()
save(fig,"07_trend.png")

# ════ 8. PCA 管道(年度 + 类别)════
fig,(a1,a2)=plt.subplots(1,2,figsize=(11,4.3))
py=pd.DataFrame(S["opportunities"]["pca_by_year"])
a1.bar(py["计划年度"].astype(str), py["total"]/1e9, color=BLUE)
a1.bar_label(a1.containers[0], fmt="%.1f", fontsize=9)
a1.set_title("年度采购计划:金额(十亿BRL)/年", fontsize=11, weight="bold"); a1.set_ylabel("十亿 BRL")
CAT_ZH={"Serviço":"服务","Obras e Serviços de Engenharia":"工程及工程服务","Material":"物资/材料",
        "Soluções de TIC":"信息通信(TIC)方案","Obra":"工程","Locação de Imóveis":"不动产租赁",
        "Serviços de Engenharia":"工程服务","Alienação/Concessão/Permissão":"转让/特许/许可"}
pc=pd.DataFrame(S["opportunities"]["pca_top_categories"]).head(6)
pc["cat_zh"]=pc["物/服"].map(lambda s: CAT_ZH.get(s, str(s)))
bars2=a2.barh(pc["cat_zh"][::-1], (pc["total"]/1e9)[::-1], color=ORANGE)
a2.bar_label(bars2, fmt="%.1f", fontsize=8, padding=2)
a2.set_xlim(0, float((pc["total"]/1e9).max())*1.18)
a2.set_title("PCA 按类别 Top6(十亿BRL)", fontsize=11, weight="bold"); a2.set_xlabel("十亿 BRL")
save(fig,"08_pca_pipeline.png")

# ════ 9. 供应商集中度:Lorenz 曲线 ════
cv=con["valor_brl_clean"].where(con["valor_brl_clean"]>0).dropna()
sup=con.assign(_v=con["valor_brl_clean"].where(con["valor_brl_clean"]>0)).groupby("中标供应商")["_v"].sum().dropna()
sv=np.sort(sup.values); cum=np.cumsum(sv)/sv.sum(); xx=np.arange(1,len(sv)+1)/len(sv)
fig,ax=plt.subplots(figsize=(6.5,5.5))
ax.plot(xx, cum, color=BLUE, lw=2, label="实际(洛伦兹曲线)")
ax.plot([0,1],[0,1],"--",color=GREY,label="完全均等")
ax.fill_between(xx, cum, xx, color=LBLUE, alpha=0.25)
g=1-2*np.trapezoid(cum,xx)
ax.set_title(f"中标供应商金额集中度(基尼≈{g:.2f})\nTop10={S['competition']['concentration']['top10_value_share_pct']:.0f}% 份额, HHI={S['competition']['concentration']['HHI']:.0f}",
    fontsize=12, weight="bold")
ax.set_xlabel("供应商累计占比(由小到大)"); ax.set_ylabel("合同金额累计占比"); ax.legend()
save(fig,"09_supplier_lorenz.png")

# ════ 10. 外企中标 by country ════
fc=pd.DataFrame(S["competition"]["foreign"]["by_country"]).sort_values("n",ascending=True)
cn={"USA":"美国","NLD":"荷兰","IRL":"爱尔兰","ESP":"西班牙","GEO":"格鲁吉亚"}
fig,ax=plt.subplots(figsize=(7,3.6))
bars=ax.barh([cn.get(c,c) for c in fc["pais"]], fc["n"], color=RED)
ax.bar_label(bars, fmt="%d", fontsize=10)
ax.set_title("外企直接中标合同数(占全部 %.3f%%,仅 %d 笔)"%(S["competition"]["foreign"]["pct_of_all"], S["competition"]["foreign"]["n_foreign_contracts"]),
    fontsize=12, weight="bold"); ax.set_xlabel("合同数")
save(fig,"10_foreign.png")

# ════ 11. 机构买方四象限(频率 × 单标均值)════
o=org[org["valor_2y_clean"].notna()&(org["valor_2y_clean"]>0)&(org["近2年招标数"]>0)].copy()
o["avg"]=o["valor_2y_clean"]/o["近2年招标数"]
o=o[o["avg"]>=1]
X_THR, Y_THR = 4, 1e6   # 高频阈值≈4标;高额阈值=100万 BRL/标
hi_n = o["近2年招标数"]>=X_THR; hi_v = o["avg"]>=Y_THR
quad = {
    "右上": (hi_n & hi_v,  GREEN,  "核心金主 · 高频高额(KA重点·可长期复购)"),
    "左上": (~hi_n & hi_v, ORANGE, "偶发大单 · 低频高额(机会型·出现即投)"),
    "右下": (hi_n & ~hi_v, BLUE,   "跑量练兵 · 高频低额(积累中标信用)"),
    "左下": (~hi_n & ~hi_v, GREY,  "长尾小标 · 低频低额(性价比低)"),
}
fig,ax=plt.subplots(figsize=(8.2,5.8))
for name,(mask,color,lab) in quad.items():
    sub=o[mask]
    ax.scatter(sub["近2年招标数"], sub["avg"], s=16, alpha=0.5, c=color, edgecolors="none",
               label=f"{lab}  n={len(sub)}")
ax.axvline(X_THR, ls="--", color="#888", lw=1); ax.axhline(Y_THR, ls="--", color="#888", lw=1)
ax.set_xscale("log"); ax.set_yscale("log")
ax.yaxis.set_major_formatter(FuncFormatter(yi_fmt))
ax.set_xlabel("近2年发标数(对数)→ 越右=复购越频繁"); ax.set_ylabel("单标均值 BRL(对数)→ 越上=单笔越大")
ax.set_title("买方机构四象限:发标频率 × 单标均值", fontsize=13, weight="bold")
ax.legend(loc="lower center", bbox_to_anchor=(0.5,-0.32), ncol=2, fontsize=8.5, frameon=False)
# 角落象限标注
ax.text(0.97,0.97,"右上",transform=ax.transAxes,ha="right",va="top",fontsize=10,color=GREEN,weight="bold")
ax.text(0.03,0.97,"左上",transform=ax.transAxes,ha="left",va="top",fontsize=10,color=ORANGE,weight="bold")
ax.text(0.97,0.03,"右下",transform=ax.transAxes,ha="right",va="bottom",fontsize=10,color=BLUE,weight="bold")
ax.text(0.03,0.03,"左下",transform=ax.transAxes,ha="left",va="bottom",fontsize=10,color=GREY,weight="bold")
save(fig,"11_org_scatter.png")

# ════ 12. 采购方式 × 维度 heatmap ════
mx=S["data_mining"]["modality_x_dimension"]
M=np.array(mx["data"]); rows=mx["index"]; cols=mx["columns"]
# 只保留 top 行(按总量)
ri=np.argsort(M.sum(1))[::-1][:8]
M2=M[ri]; rows2=[rows[i] for i in ri]
fig,ax=plt.subplots(figsize=(9,5))
im=ax.imshow(M2, aspect="auto", cmap="Blues")
ax.set_xticks(range(len(cols))); ax.set_xticklabels(cols, rotation=30, ha="right")
ax.set_yticks(range(len(rows2))); ax.set_yticklabels(rows2)
for i in range(len(rows2)):
    for j in range(len(cols)):
        if M2[i,j]>0:
            ax.text(j,i,int(M2[i,j]),ha="center",va="center",
                    color="white" if M2[i,j]>M2.max()*0.5 else "black", fontsize=8)
ax.set_title("采购方式 × 主维度 交叉(招标数)", fontsize=13, weight="bold")
fig.colorbar(im, ax=ax, fraction=0.03)
save(fig,"12_modality_x_dim.png")

# ════ 13. 在投机会 by dimension ════
d=sorted(S["opportunities"]["open_by_dimension"], key=lambda r:r["n"], reverse=True)
labels=[r["主维度"] for r in d]; ns=[r["n"] for r in d]
fig,ax=plt.subplots(figsize=(8.5,4.3))
bars=ax.bar(labels, ns, color=GREEN)
ax.bar_label(bars, fmt="%d", fontsize=9)
ax.set_title("在投机会(还能投+临近截止)按维度分布,共 %d 条"%S["opportunities"]["open_now"]["n"], fontsize=12, weight="bold")
ax.set_ylabel("在投招标数"); plt.xticks(rotation=20, ha="right")
save(fig,"13_open_by_dim.png")

# ════ 14. 可投机会过滤漏斗 ════
S2 = json.load(open("analysis/report/outputs/stats2.json", encoding="utf-8"))
fn = S2["funnel"]["stages"]
labels=[s["阶段"] for s in fn]; vals=[s["数量"] for s in fn]
fig,ax=plt.subplots(figsize=(9.5,4.8))
cols=[GREY,LBLUE,BLUE,"#1F3864",GREEN]
y=np.arange(len(labels))[::-1]
bars=ax.barh(y, vals, color=cols, height=0.62)
for yi,v in zip(y, vals):
    ax.text(v+max(vals)*0.01, yi, f"{v:,}", va="center", fontsize=10, weight="bold")
ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=9.5)
ax.set_xlim(0, max(vals)*1.15)
ax.set_title("中企可投机会过滤漏斗(逐层收窄)", fontsize=13, weight="bold")
ax.set_xlabel("招标数")
save(fig,"14_funnel.png")

# ════ 15. 本地化中标对比:外企直接 vs 本地化外资子公司 ════
loc = S2["localization"]
fd = loc["foreign_direct"]; lm = loc["localized_mnc_heuristic"]
fig,(a1,a2)=plt.subplots(1,2,figsize=(10,4.3))
cats=["外企直接\n(非本地法人)","本地化外资子公司\n(启发式·下限)"]
n_sup=[fd["n_suppliers"], lm["n_suppliers"]]
n_con=[fd["n_contracts"], lm["n_contracts"]]
x=np.arange(2); w=0.36
b1=a1.bar(x-w/2, n_sup, w, color=GREY, label="中标企业数")
b2=a1.bar(x+w/2, n_con, w, color=BLUE, label="中标合同数")
a1.bar_label(b1, fmt="%d", fontsize=9); a1.bar_label(b2, fmt="%d", fontsize=9)
a1.set_xticks(x); a1.set_xticklabels(cats, fontsize=9)
a1.set_title("外企直接 vs 本地化:中标家数/笔数", fontsize=11, weight="bold")
a1.legend(fontsize=9); a1.set_ylim(0, max(n_con)*1.2)
# 右:主体中标金额(横向条,log轴避免99%碾压看不见PF/PE)
bt = loc["by_tipo_pessoa"]
tip_zh={"PJ":"法人企业\n(PJ·可本地化主体)","PF":"自然人(PF)","PE":"其他主体(PE)"}
bt=sorted(bt, key=lambda r:(r["total"] or 0))
vals=[max(r["total"] or 0, 1) for r in bt]
lbls=[tip_zh.get(r["主体"], r["主体"]) for r in bt]
tot=sum(r["total"] or 0 for r in bt)
bars=a2.barh(range(len(bt)), vals, color=[GREY,ORANGE,BLUE], height=0.6)
for i,r in enumerate(bt):
    v=r["total"] or 0
    a2.text(v*1.15, i, f"{v/1e8:.1f}亿 ({100*v/tot:.1f}%)", va="center", fontsize=9, weight="bold")
a2.set_xscale("log")
a2.set_yticks(range(len(bt))); a2.set_yticklabels(lbls, fontsize=9)
a2.set_xlim(1e6, tot*3); a2.set_xlabel("中标金额 BRL(对数)")
a2.set_title("中标金额按供应商主体(法人占99%)", fontsize=11, weight="bold")
save(fig,"15_localization.png")

print("\n全部图表已生成至 analysis/report/outputs/charts/")
