# -*- coding: utf-8 -*-
"""Step7: 生成交付 Excel —— 中葡企业_巴西竞标匹配表_v2.xlsx。"""
import sys, io, json
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT, ROOT, TODAY

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

matches  = json.loads((OUT/"matches.json").read_text("utf-8"))
overview = json.loads((OUT/"company_overview.json").read_text("utf-8"))
unmatched= json.loads((OUT/"unmatched.json").read_text("utf-8"))
audit    = json.loads((OUT/"tender_filter_audit.json").read_text("utf-8"))
tenders  = json.loads((OUT/"tenders_clean.json").read_text("utf-8"))
near_miss= json.loads((OUT/"near_miss.json").read_text("utf-8")) if (OUT/"near_miss.json").exists() else []
rejected = json.loads((OUT/"rejected.json").read_text("utf-8")) if (OUT/"rejected.json").exists() else []
verdicts = {}
vp = OUT/"verdicts.json"
if vp.exists():
    verdicts = json.loads(vp.read_text("utf-8"))   # key: f"{cid}|{pncp}" -> {结论,说明}

# ── 总表 ──
df = pd.DataFrame(matches)
# 合并校验
def vd(r, field):
    return verdicts.get(f"{r['cid']}|{r['PNCP编号']}", {}).get(field, "")
df["校验结论"] = df.apply(lambda r: vd(r,"结论"), axis=1)
df["校验说明"] = df.apply(lambda r: vd(r,"说明"), axis=1)

COLS = ["重要程度","公司简称","行业","本地化状态","推荐优先级","首推","备选","共享首推",
        "竞标中文梗概","金额万CNY","采购方式","标的类型","截止日期","剩余天数","州","级别",
        "采购机构","价格登记SRP","长期挂网","契合分","可行性分","时效分","综合分",
        "时效渠道","落地建议","匹配理由","风险","校验结论","校验说明","PNCP编号","投标链接"]
df = df[[c for c in COLS if c in df.columns]]

# ── 总览 ──
ov = pd.DataFrame(overview)
ov["时效渠道分布"] = ov["时效渠道分布"].map(lambda d: ", ".join(f"{k}×{v}" for k,v in (d or {}).items()))
ov = ov.sort_values(["重要程度","本轮可竞标数"], ascending=[True, False])
OV_COLS = ["重要程度","公司简称","公司全称","行业","本地化状态","政采契合","是否实物供应商",
           "产能档位","单标上限万元","本地化难度","本轮可竞标数","含备选数","近端高契合待捕获",
           "首推标的","时效渠道分布","主营概括","落地提示"]
ov = ov[[c for c in OV_COLS if c in ov.columns]]

# ── 高契合·待本地化捕获 ──
nm = pd.DataFrame(near_miss)
if not nm.empty:
    nm = nm.sort_values(["imp_rank","公司简称","契合分"], ascending=[True, True, False])
    NM_COLS = ["重要程度","公司简称","行业","本地化状态","竞标中文梗概","金额万CNY","采购方式",
               "截止日期","剩余天数","州","级别","价格登记SRP","长期挂网","契合分","可行性分",
               "时效分","本轮不可投原因","捕获建议","匹配理由","PNCP编号","投标链接"]
    nm = nm[[c for c in NM_COLS if c in nm.columns]]

# ── 未匹配 ──
un = pd.DataFrame(unmatched)
if not un.empty:
    un = un.sort_values(["重要程度"])

# ── 行业未来管线建议 ──
tdf = pd.DataFrame(tenders)
pipe_rows = []
ADVICE = {
 "医疗医药":"药品/耗材/器械:完成ANVISA注册+本地代理后,入围SRP价格登记框架与卫生厅常年挂网(credenciamento),可滚动供货,规避近端截止压力。",
 "高端制造":"设备/机电:取得INMETRO等认证,通过本地组装/经销商降低关税;主攻军方(EXERCITO/MARINHA)与州级设备SRP框架。",
 "大宗商贸":"建材/食品/办公:本地化PJ法人后挂网SRP/Credenciamento;以差异化进口品类(水产/橄榄油等)切入,避开本地家庭农业专供标(PNAE须回避)。",
 "数字经济":"软硬件/数据:多为服务类,中企宜以软件许可/设备供货切入,关注联邦与州级IT设备SRP;纯本地运维服务需联合体。",
 "跨境商贸":"消费品/电商:政采渠道契合弱,宜以B2G货物供货(家电/纺织)经本地贸易商转供,提前布局PCA年度计划。",
 "文化体育":"器材/设备:仅器材供货可投(体育器材/乐器/灯光设备),活动服务类(聘演员/搭台)须回避。",
}
for dim in ["医疗医药","高端制造","大宗商贸","数字经济","跨境商贸","文化体育"]:
    sub = tdf[tdf["dim"]==dim]
    pipe_rows.append({
        "行业": dim,
        "可投标池数": int(len(sub)),
        "其中SRP价格登记": int(sub["srp_bool"].sum()) if "srp_bool" in sub else 0,
        "其中长期挂网": int(sub["lt_bool"].sum()) if "lt_bool" in sub else 0,
        "货物类占比": f"{(sub['obj_type'].eq('goods').mean()*100):.0f}%" if len(sub) else "-",
        "未来管线建议": ADVICE.get(dim,""),
    })
pipe = pd.DataFrame(pipe_rows)

# ── 方法论 ──
method = pd.DataFrame([
 ("数据来源","巴西PNCP政府采购门户(report.xlsx 招标主表 8357 笔) + 中葡企业机构数据库(437家去重)"),
 ("时效基准日", TODAY),
 ("评分引擎","DeepSeek deepseek-v4-flash(企业画像 + 逐标契合/可行/时效打分)"),
 ("过滤口径", " | ".join(f"{k}:{v}" for k,v in audit.items())),
 ("综合分公式","综合分 = 0.45×契合 + 0.30×可行性 + 0.25×时效 (各0-10)"),
 ("契合度","标的货物须与企业主营产品对得上;同行业但产品对不上 → 低分"),
 ("可行性","金额超企业产能/价值上限 → 低分;本地工程(obra)已硬剔除;本地人力服务 → 低分"),
 ("时效性","需本地化企业无CNPJ不能直投近端截止标,偏好SRP价格登记/长期挂网(credenciamento)/远期;已在巴西企业可直投"),
 ("重要程度排序","1高>2中>3低>无标识;配额 高8/中6/低4/无3;优质标的『★首推』软独占给最重要企业"),
 ("首推标记","★首推=该标在所有匹配企业中,优先推荐给最重要企业;『共享首推』列标注被更高优先级企业占用的情况"),
 ("校验","对高/中重要度企业 top 匹配做多智能体对抗校验(行业错配/价值超限/时效不可行/服务工程误入)"),
 ("免责","金额含数据坑(保密=预算未披露;>5亿已剔除);投标链接来自PNCP link_sistema_origem,投标前须核对原始Edital"),
], columns=["维度","说明"])

# ── 写出 + 样式 ──
out_path = ROOT/"data/exports/中葡企业_巴西竞标匹配表_v2.xlsx"
with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
    df.to_excel(xw, sheet_name="竞标匹配总表(本轮可投)", index=False)
    ov.to_excel(xw, sheet_name="企业匹配总览", index=False)
    if not nm.empty:
        nm.to_excel(xw, sheet_name="高契合·待本地化捕获", index=False)
    if not un.empty:
        un.to_excel(xw, sheet_name="弱契合与未匹配", index=False)
    rj = pd.DataFrame(rejected)
    if not rj.empty:
        rj = rj.sort_values(["imp_rank","公司简称"]).drop(columns=["imp_rank"], errors="ignore")
        rj.to_excel(xw, sheet_name="对抗校验剔除", index=False)
    pipe.to_excel(xw, sheet_name="行业未来管线建议", index=False)
    method.to_excel(xw, sheet_name="方法论与口径", index=False)

    wb = xw.book
    hdr_fill = PatternFill("solid", fgColor="1F4E78")
    hdr_font = Font(color="FFFFFF", bold=True, size=10)
    imp_fill = {"1高":"FFC7CE","2中":"FFEB9C","3低":"D9E1F2","无标识(最低)":"F2F2F2"}
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin,right=thin,top=thin,bottom=thin)
    WIDTHS = {"竞标中文梗概":48,"匹配理由":40,"落地建议":28,"风险":24,"采购机构":26,"采购方式":18,
              "投标链接":34,"PNCP编号":24,"主营概括":24,"落地提示":26,"未来管线建议":60,"说明":80,
              "首推标的":40,"时效渠道分布":24,"校验说明":34}
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for ci, cell in enumerate(ws[1], 1):
            cell.fill = hdr_fill; cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        # 列宽
        for ci in range(1, ws.max_column+1):
            name = ws.cell(1, ci).value
            w = WIDTHS.get(name, 12)
            ws.column_dimensions[get_column_letter(ci)].width = w
        # 行高 + wrap + 重要程度底色(仅总表/总览)
        imp_col = head_col = vd_col = nm_col = None
        for ci in range(1, ws.max_column+1):
            v = ws.cell(1,ci).value
            if v == "重要程度": imp_col = ci
            elif v == "首推": head_col = ci
            elif v == "校验结论": vd_col = ci
            elif v == "近端高契合待捕获": nm_col = ci
        vd_fill = {"剔除":"FFC7CE", "降级":"FFEB9C", "通过":"C6EFCE"}
        head_fill = PatternFill("solid", fgColor="FFF2CC")
        for ri in range(2, ws.max_row+1):
            for ci in range(1, ws.max_column+1):
                cell = ws.cell(ri,ci)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.font = Font(size=9)
            if imp_col:
                v = ws.cell(ri, imp_col).value
                if v in imp_fill:
                    ws.cell(ri, imp_col).fill = PatternFill("solid", fgColor=imp_fill[v])
            if head_col and str(ws.cell(ri, head_col).value or "").strip():
                ws.cell(ri, head_col).fill = head_fill
                ws.cell(ri, head_col).font = Font(size=9, bold=True, color="BF8F00")
            if vd_col:
                v = str(ws.cell(ri, vd_col).value or "").strip()
                if v in vd_fill:
                    ws.cell(ri, vd_col).fill = PatternFill("solid", fgColor=vd_fill[v])

print("已生成:", out_path)
print("总表行数:", len(df), "| 总览:", len(ov), "| 未匹配:", len(un), "| 已合并校验:", bool(verdicts))
