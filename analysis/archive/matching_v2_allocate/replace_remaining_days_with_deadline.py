# -*- coding: utf-8 -*-
"""把三分类主表的『剩余天数』列改为『提交截止日期』(用 report.xlsx 招标主表的真实 投标截止,按 PNCP 连接)。
   注意:剩余天数基准日=2026-05-29(采集快照日),绝不用 今天+剩余天数。直接取源表真实截止日。"""
import sys, io
import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.comments import Comment

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = "data/exports/report.xlsx"
TARGET = "data/exports/中葡企业_巴西竞标匹配_三分类主表.xlsx"
OUT = "data/exports/中葡企业_巴西竞标匹配_三分类主表_v2.xlsx"
NEW_HEADER = "提交截止日期"

# 1) PNCP -> 真实投标截止 datetime
t = pd.read_excel(SRC, sheet_name="招标主表")
deadline = {}
for _, r in t.iterrows():
    deadline[str(r["PNCP编号"])] = r["投标截止"]

def fmt(dt):
    if pd.isna(dt):
        return None
    dt = pd.to_datetime(dt)
    hm = (dt.hour, dt.minute)  # 忽略秒,避免 00:00:01 / 23:59:59 边界漏判
    # 午夜(00:00)或当日终(23:59)视为"当日截止",只显日期;其余显日期+时分
    if hm in [(0, 0), (23, 59)]:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M")

# 2) 改表(保留全部格式)
wb = load_workbook(TARGET)
ws = wb.active
hdr = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1)}
kcol = hdr["剩余天数"]; pcol = hdr["PNCP编号"]
ws.cell(1, kcol).value = NEW_HEADER  # 仅改表头文字,保留表头样式
ws.cell(1, kcol).comment = Comment(
    "取自源表(report.xlsx 招标主表)真实『投标截止』,为巴西利亚当地时间;"
    "原『剩余天数』以采集快照日 2026-05-29 为基准,本列 = 该基准日 + 剩余天数;"
    "『无截止』为长期挂网 / 价格登记类(随时可投,见相邻两列)。",
    "Claude")

n_open = n_date = n_keep = 0
report_rows = []
for r in range(2, ws.max_row + 1):
    cell = ws.cell(r, kcol)
    cur = cell.value
    pncp = str(ws.cell(r, pcol).value)
    if cur in ("无截止", None, ""):
        cell.value = "无截止"; n_open += 1
        report_rows.append((pncp, cur, "无截止")); continue
    dl = deadline.get(pncp)
    f = fmt(dl)
    if f is None:
        # 源表无截止日期 -> 保持原"剩余天数(截至采集日2026-05-29)"以免丢信息
        cell.value = f"{cur}天(采集日2026-05-29起)"; n_keep += 1
        report_rows.append((pncp, cur, cell.value))
    else:
        cell.value = f; n_date += 1
        report_rows.append((pncp, cur, f))

# 列宽放宽以容纳"YYYY-MM-DD HH:MM"
ws.column_dimensions[get_column_letter(kcol)].width = 16

wb.save(OUT)
print(f"已写出: {OUT}")
print(f"表头 第{get_column_letter(kcol)}列 '剩余天数' -> '{NEW_HEADER}'")
print(f"真实截止日 {n_date} 行 | 无截止 {n_open} 行 | 源无日期回退 {n_keep} 行 | 合计 {n_date+n_open+n_keep}")
print("样例(PNCP, 原剩余天数, 新截止):")
for p, a, b in report_rows[:8]:
    print(f"  {p}  {str(a):>6}  ->  {b}")
