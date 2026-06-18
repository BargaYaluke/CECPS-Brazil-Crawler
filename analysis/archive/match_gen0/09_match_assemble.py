# -*- coding: utf-8 -*-
"""
09_match_assemble.py — 汇总多智能体匹配结果 → Excel匹配表 + Word策略报告
读取: analysis/outputs/match/results/c*.json (匹配) + v*.json (1高对抗校验)
      analysis/outputs/match/companies.json (企业画像)
      data/procurement.db (回填权威竞标字段)
输出: data/exports/中葡企业_巴西竞标匹配表.xlsx
      analysis/outputs/中葡企业_巴西竞标匹配策略报告.docx
"""
import os, json, sqlite3, datetime, collections
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

ROOT = r"C:\巴西爬虫"
OUT  = os.path.join(ROOT, "analysis", "outputs", "match")
RES  = os.path.join(OUT, "results")
DB   = os.path.join(ROOT, "data", "procurement.db")
XLSX_OUT = os.path.join(ROOT, "data", "exports", "中葡企业_巴西竞标匹配表.xlsx")
DOCX_OUT = os.path.join(ROOT, "analysis", "outputs", "中葡企业_巴西竞标匹配策略报告.docx")
TODAY = datetime.date(2026, 6, 9)
DIM2ZH = {'high_end_manufacturing':'高端制造','healthcare':'医疗医药','digital_economy':'数字经济',
 'commodities':'大宗商贸','culture_sports':'文化体育','cross_border_ecommerce':'跨境商贸','None':'未分类'}

def jload(p, default=None):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except Exception: return default

# --------------------------------------------------- 回填权威竞标字段
def bid_lookup(pncp_ids):
    if not pncp_ids: return {}
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; cur = con.cursor()
    trans = {r[0]: r[1] for r in cur.execute("SELECT source_text,translated FROM translation_cache")}
    out = {}
    q = ("SELECT pncp_id,objeto_compra,valor_cny_estimado,valor_total_estimado,"
         "data_encerramento_proposta,modalidade_nome,uf_sigla,municipio_nome,"
         "orgao_razao_social,link_sistema_origem FROM contratacoes WHERE pncp_id=?")
    for pid in pncp_ids:
        r = cur.execute(q, (pid,)).fetchone()
        if not r: continue
        try:
            dl = datetime.datetime.fromisoformat(str(r['data_encerramento_proposta'])).date()
            dias = (dl - TODAY).days; dls = str(dl)
        except Exception:
            dias = None; dls = None
        obj = r['objeto_compra'] or ''
        out[pid] = dict(objeto_zh=(trans.get(obj) or '')[:200], objeto_pt=obj[:300],
            valor_cny=r['valor_cny_estimado'] or 0, valor_brl=r['valor_total_estimado'] or 0,
            deadline=dls, dias=dias, modalidade=r['modalidade_nome'],
            uf=r['uf_sigla'], municipio=r['municipio_nome'],
            organ=r['orgao_razao_social'], link=r['link_sistema_origem'])
    con.close(); return out

# --------------------------------------------------- 载入并融合结果
def load_results(comps):
    rows = []                       # 推荐明细行
    overview = []                   # 企业总览行
    missing = []                    # 缺结果文件的企业
    all_pids = set()
    raw = {}
    for c in comps:
        m = jload(os.path.join(RES, f"c{c['idx']:04d}.json"))
        if not m:
            missing.append(c); continue
        v = jload(os.path.join(RES, f"v{c['idx']:04d}.json"))
        raw[c['idx']] = (c, m, v)
        for rec in (m.get('recommendations') or []):
            pid = rec.get('pncp_id')
            if pid: all_pids.add(pid)
    blu = bid_lookup(all_pids)
    for idx, (c, m, v) in raw.items():
        # 校验裁决
        verdict = {}
        revised_fit = None
        if v:
            for vd in (v.get('verdicts') or []):
                verdict[vd.get('pncp_id')] = (vd.get('verdict'), vd.get('reason'))
            revised_fit = v.get('revised_gov_fit')
        gov_fit = revised_fit or m.get('gov_fit')
        recs = []
        for rec in (m.get('recommendations') or []):
            pid = rec.get('pncp_id')
            vv = verdict.get(pid, (None, None))
            if vv[0] == '剔除':      # 对抗校验剔除
                continue
            b = blu.get(pid, {})
            recs.append(dict(rec=rec, bid=b, verdict=vv[0], vreason=vv[1]))
        # 排序: priority 升序, 校验降级靠后
        recs.sort(key=lambda x: (1 if x['verdict']=='降级' else 0, x['rec'].get('priority',9)))
        overview.append(dict(c=c, m=m, v=v, gov_fit=gov_fit, n_recs=len(recs)))
        for rank_i, rr in enumerate(recs, 1):
            rec, b = rr['rec'], rr['bid']
            rows.append(dict(
                rank=c['rank'], importance=c['importance'], short=c['short'], full=c['full'],
                dim_zh=DIM2ZH.get(c['dim'], c['dim']), gov_fit=gov_fit, cap=c['capacity_tier'],
                priority=rec.get('priority'), pncp=rec.get('pncp_id'),
                objeto_zh=b.get('objeto_zh') or rec.get('objeto_zh') or '',
                valor_cny=b.get('valor_cny') or rec.get('valor_cny') or 0,
                valor_brl=b.get('valor_brl') or 0,
                deadline=b.get('deadline') or rec.get('deadline') or '',
                dias=b.get('dias'), uf=b.get('uf') or rec.get('uf') or '',
                organ=b.get('organ') or rec.get('organ') or '',
                modalidade=b.get('modalidade') or '',
                product_match=rec.get('product_match') or '',
                feasibility=rec.get('feasibility') or '',
                value_fit=rec.get('value_fit') or '',
                channel=rec.get('timeliness_channel') or '',
                rationale=rec.get('rationale') or '',
                verdict=rr['verdict'] or '', vreason=rr['vreason'] or '',
                link=b.get('link') or rec.get('link') or '',
            ))
    return rows, overview, missing

# --------------------------------------------------- Excel
HDR_FILL = PatternFill('solid', fgColor='1F4E78')
HDR_FONT = Font(color='FFFFFF', bold=True, size=10)
IMP_FILL = {'1高':'C00000','2中':'ED7D31','3低':'FFC000','无标识':'BFBFBF'}
FIT_FILL = {'强':'C6EFCE','中':'FFEB9C','弱':'FCE4D6','几乎无':'F2F2F2'}
THIN = Border(*[Side(style='thin', color='D9D9D9')]*4)

def style_header(ws, ncol, row=1):
    for c in range(1, ncol+1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HDR_FILL; cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

def build_excel(rows, overview, missing, comps):
    wb = openpyxl.Workbook()
    # ---- Sheet1 匹配总表
    ws = wb.active; ws.title = '竞标匹配总表'
    cols = ['重要程度','公司简称','行业','政采契合度','推荐优先级','竞标中文梗概','金额(万CNY)',
            '截止日期','剩余天数','州','采购机构','产品对应','可行性','金额匹配','时效渠道','匹配理由',
            '校验','校验说明','PNCP编号','投标链接']
    ws.append(cols); style_header(ws, len(cols))
    for r in rows:
        ws.append([r['importance'], r['short'], r['dim_zh'], r['gov_fit'], r['priority'],
            r['objeto_zh'], round((r['valor_cny'] or 0)/10000, 1), r['deadline'], r['dias'],
            r['uf'], r['organ'], r['product_match'], r['feasibility'], r['value_fit'],
            r['channel'], r['rationale'], r['verdict'], r['vreason'], r['pncp'], r['link']])
        ri = ws.max_row
        f = IMP_FILL.get(r['importance'])
        if f: ws.cell(row=ri, column=1).fill = PatternFill('solid', fgColor=f); ws.cell(row=ri,column=1).font=Font(color='FFFFFF',bold=True)
        ff = FIT_FILL.get(r['gov_fit'])
        if ff: ws.cell(row=ri, column=4).fill = PatternFill('solid', fgColor=ff)
    widths = [9,14,9,9,8,40,11,11,8,5,26,22,16,12,20,46,7,28,26,40]
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
    for row in ws.iter_rows(min_row=2):
        for cell in row: cell.alignment = Alignment(vertical='top', wrap_text=True); cell.border=THIN

    # ---- Sheet2 企业总览
    ws2 = wb.create_sheet('企业总览')
    cols2 = ['重要程度','公司简称','公司全称','行业','政采契合度','产能评估','契合判断',
             '推荐竞标数','未来管道(PCA)建议','风险提示','对抗校验质量']
    ws2.append(cols2); style_header(ws2, len(cols2))
    ov_sorted = sorted(overview, key=lambda o: (-o['c']['rank'], o['c']['dim'], o['c']['short']))
    for o in ov_sorted:
        c, m, v = o['c'], o['m'], o['v']
        ws2.append([c['importance'], c['short'], c['full'], DIM2ZH.get(c['dim'],c['dim']),
            o['gov_fit'], m.get('capacity_assessment',''), m.get('fit_reason',''), o['n_recs'],
            m.get('future_pipeline',''), m.get('notes',''),
            (v.get('overall_quality','') if v else '')])
        ri = ws2.max_row
        f = IMP_FILL.get(c['importance'])
        if f: ws2.cell(row=ri,column=1).fill=PatternFill('solid',fgColor=f); ws2.cell(row=ri,column=1).font=Font(color='FFFFFF',bold=True)
        ff = FIT_FILL.get(o['gov_fit'])
        if ff: ws2.cell(row=ri,column=5).fill=PatternFill('solid',fgColor=ff)
    widths2=[9,14,30,9,9,46,40,9,46,40,12]
    for i,w in enumerate(widths2,1): ws2.column_dimensions[get_column_letter(i)].width=w
    ws2.freeze_panes='A2'; ws2.auto_filter.ref=ws2.dimensions
    for row in ws2.iter_rows(min_row=2):
        for cell in row: cell.alignment=Alignment(vertical='top',wrap_text=True); cell.border=THIN

    # ---- Sheet3 弱契合/缺结果
    ws3 = wb.create_sheet('弱契合与待补')
    ws3.append(['类别','重要程度','公司简称','行业','政采契合度','说明']); style_header(ws3,6)
    for o in ov_sorted:
        if o['gov_fit'] in ('弱','几乎无') or o['n_recs']==0:
            c=o['c']
            ws3.append(['弱契合',c['importance'],c['short'],DIM2ZH.get(c['dim'],c['dim']),
                o['gov_fit'], o['m'].get('fit_reason','')])
    for c in sorted(missing,key=lambda x:(-x['rank'])):
        ws3.append(['缺匹配结果',c['importance'],c['short'],DIM2ZH.get(c['dim'],c['dim']),'-','匹配Agent未产出(可重跑)'])
    for i,w in enumerate([12,9,14,9,9,60],1): ws3.column_dimensions[get_column_letter(i)].width=w
    ws3.freeze_panes='A2'
    for row in ws3.iter_rows(min_row=2):
        for cell in row: cell.alignment=Alignment(vertical='top',wrap_text=True)

    # ---- Sheet4 方法论
    ws4 = wb.create_sheet('方法论与口径')
    notes = [
     ['维度','说明'],
     ['数据来源','巴西PNCP政府采购门户(采集窗口2026-05-22~05-28, 8357笔), 企业=中葡企业机构数据库438家'],
     ['时效基准日', str(TODAY)],
     ['可行性过滤','只保留"供货"(aquisição/fornecimento), 剔除本地工程(obra)与本地人力服务(维修/运输/清洁/安保/培训/金融/医疗人力)'],
     ['行业映射','高端制造/医疗医药/数字经济/大宗商贸/文化体育/跨境商贸 → PNCP六维度'],
     ['匹配桥接','中文主营业务↔葡文标的: 双语子类目(药品/医疗设备/车辆/充电/工程机械/电力/IT软硬件/安防等)语义对接 + 大模型语义复核'],
     ['重要程度','1高(50家)>2中(65)>3低(18)>无标识(305); 高优企业经独立对抗校验剔除不现实匹配'],
     ['时效渠道','near(<30天)未本地化只能联合体/转PCA; long(>90天或长期)可边本地化边备标; 本地化通常需3-6个月'],
     ['产能匹配','按企业规模(上市/集团/子公司等信号)估产能软上限, 远超产能的大单建议联合体/分包或降级'],
     ['局限','招标为单周快照不可年化; 金额为预估; 本地化周期/中标率需外部尽调; 子类目为启发式, 个别误配以大模型复核与人工二次确认为准'],
    ]
    for r in notes: ws4.append(r)
    style_header(ws4,2)
    ws4.column_dimensions['A'].width=14; ws4.column_dimensions['B'].width=110
    for row in ws4.iter_rows(min_row=2):
        for cell in row: cell.alignment=Alignment(vertical='top',wrap_text=True)
    wb.save(XLSX_OUT)
    return len(rows), len(overview)

# --------------------------------------------------- main
def main():
    comps = jload(os.path.join(OUT,'companies.json'), [])
    rows, overview, missing = load_results(comps)
    nrows, novr = build_excel(rows, overview, missing, comps)
    # 统计
    fit = collections.Counter(o['gov_fit'] for o in overview)
    summary = dict(n_companies=len(comps), n_with_results=len(overview), n_missing=len(missing),
        n_recommendation_rows=nrows, gov_fit=dict(fit),
        missing=[c['short'] for c in missing][:30])
    with open(os.path.join(OUT,'assemble_summary.json'),'w',encoding='utf-8') as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)
    # 供Word报告使用的合并数据
    ov_dump = [dict(idx=o['c']['idx'], short=o['c']['short'], full=o['c']['full'],
        dim=o['c']['dim'], importance=o['c']['importance'], rank=o['c']['rank'],
        cap=o['c']['capacity_tier'], gov_fit=o['gov_fit'], n_recs=o['n_recs'],
        capacity_assessment=o['m'].get('capacity_assessment',''),
        fit_reason=o['m'].get('fit_reason',''), future_pipeline=o['m'].get('future_pipeline',''),
        notes=o['m'].get('notes',''),
        quality=(o['v'].get('overall_quality','') if o['v'] else '')) for o in overview]
    with open(os.path.join(OUT,'consolidated.json'),'w',encoding='utf-8') as f:
        json.dump(dict(rows=rows, overview=ov_dump,
            missing=[dict(short=c['short'],importance=c['importance'],dim=c['dim']) for c in missing]),
            f, ensure_ascii=False, indent=1)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print("Excel ->", XLSX_OUT)

if __name__ == '__main__':
    main()
