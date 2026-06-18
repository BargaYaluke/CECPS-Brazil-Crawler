# -*- coding: utf-8 -*-
"""
08_match_prepare.py  —  企业 × 巴西政采竞标 匹配引擎（数据清洗 + 候选生成）

输出目录: analysis/outputs/match/
  companies.json        清洗后的 438 家企业（维度/重要度/子类目/产能先验/画像）
  pool_<dim>.json       每个维度的"可行供货"竞标池（可行性+子类目分类，附中文梗概）
  candidates/<idx>.json 每家企业的候选竞标池（双语子类目桥接 + 时效三时间轴）
  pca_<dim>.json        每个维度的 PCA 未来管道概览
  manifest.json         给工作流用的企业索引（含候选文件路径、优先级分层）
  prepare_stats.json    清洗/匹配统计
"""
import sqlite3, json, re, os, unicodedata, datetime, collections
import openpyxl

ROOT   = r"C:\巴西爬虫"
DB     = os.path.join(ROOT, "data", "procurement.db")
XLSX   = os.path.join(ROOT, "data", "exports", "中葡企业机构数据库_企业登记表_六大行业顺序.xlsx")
OUT    = os.path.join(ROOT, "analysis", "outputs", "match")
CAND   = os.path.join(OUT, "candidates")
os.makedirs(CAND, exist_ok=True)
TODAY  = datetime.date(2026, 6, 9)

# ---------------------------------------------------------------- 行业映射
IND2DIM = {
    '高端制造': 'high_end_manufacturing',
    '医疗医药': 'healthcare',
    '数字经济': 'digital_economy',
    '大宗商贸': 'commodities',
    '文化体育': 'culture_sports',
    '跨境商贸': 'cross_border_ecommerce',
}
DIM2ZH = {v: k for k, v in IND2DIM.items()}
DIM2ZH['None'] = '未分类'

IMP_RANK = {'1高': 4, '2中': 3, '3低': 2}          # 无标识 -> 1
IMP_LABEL = {4: '1高', 3: '2中', 2: '3低', 1: '无标识'}

# ---------------------------------------------------------------- 双语子类目
# 每个子类目: zh(企业主营业务关键词) / pt(招标objeto关键词) / 典型金额量级提示
SUBCATS = {
 'medicamentos':        dict(zh=['药品','医药','口服液','片剂','制剂','原料药','疫苗','中药','注射液','胶囊'],
                             pt=['medicament','farmac','vacina','insulina','antibiotic','soro ','droga ','principio ativo']),
 'equip_medico':        dict(zh=['医疗设备','医疗器械','手术','影像','监护','诊断','超声','ct','核磁','检验设备','口腔设备'],
                             pt=['equipamento medico','equipamento hospitalar','equipamento odonto','ultrassom','tomografo',
                                 'raio-x','raio x','monitor multiparametro','ventilador pulmonar','equipamento laborator',
                                 'desfibrilador','autoclave','equipamento de imagem']),
 'material_hospitalar': dict(zh=['医用耗材','耗材','试剂','防护','口罩','手套','敷料','导管','注射器','检测试剂','诊断试剂'],
                             pt=['material hospitalar','material medico','insumo','reagente','seringa','luva','curativo',
                                 'cateter','material penso','material laborator','equipamento de protecao','correlato']),
 'mobiliario_hospitalar':dict(zh=['病床','轮椅','医疗家具','护理床','担架'],
                             pt=['cadeira de rodas','cadeira higien','leito hospitalar','cama hospitalar','maca ','mobiliario hospitalar']),
 'veiculos':            dict(zh=['汽车','商用车','卡车','重卡','轻卡','van','客车','整车','乘用车','面包车','皮卡','巴士','大巴'],
                             pt=['veiculo','automovel','caminhao','onibus','van ','micro-onibus','ambulanc','caminhonete',
                                 'aquisicao de carro','frota','furgao','utilitario']),
 'veiculos_eletricos':  dict(zh=['新能源汽车','电动汽车','电动车','充电桩','充电','换电','电动自行车','e-bike','两轮','电助力','氢燃料'],
                             pt=['veiculo eletrico','carregador eletric','estacao de recarga','eletroposto','bicicleta eletric',
                                 'patinete','onibus eletric']),
 'maquinas_pesadas':    dict(zh=['工程机械','起重','挖掘机','装载机','叉车','拖拉机','农机','机器人','agv','amr','工业车辆','压路机'],
                             pt=['trator','retroescavadeira','escavadeira','motoniveladora','pa carregadeira','empilhadeira',
                                 'maquina agricola','rolo compactador','equipamento agricola','implemento agricola','robo ']),
 'equip_energia':       dict(zh=['电力设备','变压器','发电机','储能','光伏','太阳能','输配电','电网','开关柜','ups','逆变器'],
                             pt=['transformador','gerador','grupo gerador','energia solar','fotovoltaic','painel solar',
                                 'subestacao','no-break','nobreak','sistema de energia','bateria estacionar']),
 'climatizacao':        dict(zh=['空调','暖通','制冷','通风','冷链','冷库'],
                             pt=['ar-condicionado','ar condicionado','climatizacao','climatizador','sistema de refrigeracao',
                                 'condicionador de ar','camara fria','ventilacao e exaustao']),
 'ti_hardware':         dict(zh=['服务器','电脑','计算机','笔记本','数据中心','存储','网络设备','打印机','平板','硬件'],
                             pt=['computador','notebook','servidor','microcomputador','data center','storage','impressora',
                                 'switch','roteador','equipamento de informatica','tablet','workstation','desktop']),
 'ti_software':         dict(zh=['软件','信息系统','管理系统','操作系统','应用软件','saas','信息化','数字化转型','云计算','大数据','人工智能','教育科技','erp','it服务','数智化','智慧教育','政务系统'],
                             pt=['software','licenca de uso','sistema de gestao','solucao de ti','plataforma digital',
                                 'erp','licenciamento','sistema informatizado','solucao de software','solucao tecnolog']),
 'seguranca_eletronica':dict(zh=['安防','监控','视频监控','门禁','摄像','人脸','x光','安检','威视','报警','传感器'],
                             pt=['monitoramento eletronico','videomonitoramento','cftv','camera','circuito fechado',
                                 'controle de acesso','reconhecimento facial','rastreamento eletronico','scanner corporal',
                                 'alarme','sensor','vigilancia eletronica']),
 'telecom':             dict(zh=['通信','通讯','基站','光纤','5g','射频','天线','对讲'],
                             pt=['telecomunicacao','radiocomunicacao','fibra otica','link de dados','telefonia',
                                 'antena','radio comunicador','enlace']),
 'mobiliario_geral':    dict(zh=['家具','办公家具','桌椅','货架'],
                             pt=['mobiliario','moveis','cadeira','mesa ','armario','estante','arquivo deslizante']),
 'material_esportivo':  dict(zh=['体育器材','运动器材','健身器材','体育用品','球类'],
                             pt=['material esportivo','equipamento esportivo','academia ','playground','material desportivo',
                                 'equipamento de ginastica','quadra ']),
 'eletro_eletronico':   dict(zh=['家电','电器','电子产品','消费电子','小家电','显示','led'],
                             pt=['eletrodomestico','eletroeletronico','aparelho de tv','geladeira','refrigerador',
                                 'aparelho eletronico','painel de led','televisor']),
 'textil_vestuario':    dict(zh=['服装','纺织','制服','鞋','箱包','面料','针织','床品','工装'],
                             pt=['vestuario','uniforme','fardamento','tecido','calcado','confeccao','enxoval','roupa ',
                                 'textil','cama mesa e banho']),
 'alimentos':           dict(zh=['食品','农产品','粮油','饮料','食材','生鲜'],
                             pt=['genero alimenticio','alimento','cesta basica','merenda','hortifruti','agua mineral']),
 'material_construcao':  dict(zh=['建材','建筑材料','水泥','钢材','管材','照明','五金','油漆','水电材料'],
                             pt=['material de construcao','material eletric','material hidraulic','iluminacao publica',
                                 'luminaria','tubo ','ferragem','tinta ','cimento','material de pintura']),
 'lab_cientifico':      dict(zh=['实验室','科研','检测仪器','分析仪','试验设备'],
                             pt=['equipamento de laboratorio','material de laboratorio','reagente laborator','aparelho cientific']),
}
# 维度 -> 缺省可考虑的子类目（当企业主营业务未命中具体子类目时）
DIM_DEFAULT_SUB = {
 'healthcare': ['medicamentos','equip_medico','material_hospitalar','mobiliario_hospitalar','lab_cientifico'],
 'high_end_manufacturing': ['veiculos','veiculos_eletricos','maquinas_pesadas','equip_energia','climatizacao','ti_hardware'],
 'digital_economy': ['ti_hardware','ti_software','seguranca_eletronica','telecom'],
 'commodities': ['material_construcao','equip_energia','maquinas_pesadas','mobiliario_geral','veiculos'],
 'culture_sports': ['material_esportivo','mobiliario_geral'],
 'cross_border_ecommerce': ['textil_vestuario','eletro_eletronico','alimentos','mobiliario_geral'],
 'None': [],
}

# ---------------------------------------------------------------- 文本规整
def norm(s):
    s = s or ""
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()

# 可行性: 不可行(本地工程/本地人力服务) vs 可行(供货)
INFEAS = [r'\bobra', r'pavimenta', r'constru[ci]', r'reforma', r'revitaliza', r'reestrutura', r'edifica',
 r'drenagem', r'terraplan', r'recapea', r'engenharia civil', r'manutencao rodoviar', r'manutencao predial',
 r'manutencao de via', r'limpeza urbana', r'limpeza predial', r'limpeza e conserv', r'vigilanci',
 r'seguranca patrimonial', r'conservacao predial', r'copeiragem', r'jardinagem', r'\bportaria\b',
 r'recepcionist', r'transporte de (servidor|pessoa|aluno|estudante|paciente|usuario)', r'\bmotorista',
 r'mao de obra', r'servicos medicos', r'servicos de saude', r'prestacao de servicos medic',
 r'servicos financeiros', r'folha de pagamento', r'instituicao financeira',
 r'com operador', r'com motorista', r'\bpublicidade', r'\beventos?\b', r'\bshows?\b', r'festival',
 r'sao joao', r'patrocin', r'alienacao', r'concessao', r'locacao de imovel', r'coleta de (lixo|residuo)',
 r'capina', r'roca', r'desassoreamento', r'sinalizacao viaria', r'servicos de engenharia',
 r'mananciais', r'abastecimento de agua\b', r'esgotamento sanitar', r'servico continuo de']
# 可行性以"供货动作词"为准(光提到某物≠采购它), 再加少量天然为货物的名词
FEAS = [r'aquisicao', r'fornecimento', r'\bcompra de', r'\bcompras de', r'registro de preco', r'\bsrp\b',
 r'aquisicao de', r'fornecimento de', r'para fornecimento', r'eventual aquisicao',
 # 天然货物名词(无歧义, 即使无动作词也算供货)
 r'medicament', r'insumo', r'reagente', r'uniforme', r'fardamento', r'material esportivo',
 r'genero alimenticio', r'cesta basica', r'material de consumo', r'material permanente',
 r'material hospitalar', r'mobiliario']
# 强不可行: 命中即判不可行(覆盖供货词) —— 本地租赁/带操作员/燃油/培训等本地服务,中企无法供货承接
STRONG_INFEAS = [
 r'\bobra', r'pavimenta', r'constru[ci]', r'reforma', r'revitaliza', r'reestrutura', r'edifica',
 r'engenharia civil', r'servicos de engenharia', r'manutencao rodoviar', r'manutencao predial',
 r'servicos medicos', r'servicos de saude', r'folha de pagamento', r'servicos financeiros', r'instituicao financeira',
 r'(com|incluindo|inclus[oa]s?|munid[oa]s? de|acompanhad[oa]s?) (operador|motorista|condutor)',
 r'motorista\s*/\s*operador', r'operador\s*/\s*motorista', r'\bcom condutor',
 r'loca[c\w]+ de (veiculo|maquina|caminhao|equipament|trator|onibus|van|frota|escavad|retroescav|patrol|motoniv)',
 r'\baluguel de (veiculo|maquina|caminhao|equipament|trator|onibus|van)',
 r'\bcombustivel', r'\bgasolina', r'\bdiesel\b', r'oleo diesel', r'\betanol\b', r'arla 32',
 r'capacitacao', r'treinamento', r'curso de ', r'qualificacao profissional', r'formacao de ',
 r'limpeza urbana', r'limpeza predial', r'limpeza e conserv', r'vigilanci', r'seguranca patrimonial',
 r'transporte de (servidor|pessoa|aluno|estudante|paciente|usuario)', r'\bpublicidade', r'\beventos?\b',
 r'festival', r'sao joao', r'patrocin', r'alienacao', r'concessao', r'locacao de imovel',
 r'coleta de (lixo|residuo)', r'mao de obra', r'\bshows?\b',
 r'manutencao (de |corretiva|preventiva|predial|veicular)', r'servicos? de manutencao',
 r'\breboque', r'\boficina', r'borracharia', r'lavagem de veiculo', r'guincho',
 r'manutencao de frota', r'manutencao de (veiculo|maquina|equipament)', r'revisao de veiculo',
]
INF_RE = [re.compile(p) for p in INFEAS]
FEA_RE = [re.compile(p) for p in FEAS]
STRONG_RE = [re.compile(p) for p in STRONG_INFEAS]

def feasibility(objeto):
    t = norm(objeto)
    if any(p.search(t) for p in STRONG_RE):
        return 'infeasible'
    inf = any(p.search(t) for p in INF_RE)
    fe  = any(p.search(t) for p in FEA_RE)
    if fe and not inf: return 'feasible'
    if fe and inf:     return 'mixed'
    if inf and not fe: return 'infeasible'
    return 'unknown'

# 预编译子类目 pt 正则
SUB_PT_RE = {k: [re.compile(re.escape(w)) for w in v['pt']] for k, v in SUBCATS.items()}
def bid_subcats(objeto):
    t = norm(objeto)
    hit = [k for k, pats in SUB_PT_RE.items() if any(p.search(t) for p in pats)]
    return hit

def company_subcats(text):
    t = (text or '').lower()
    hit = [k for k, v in SUBCATS.items() if any(w.lower() in t for w in v['zh'])]
    return hit

# 产能先验：从公司全称+主营业务推断可承接金额量级
BIG_SIGNALS = ['上市','股份有限公司','集团','控股','央企','国企','国资','港股','纽交所','创业板','a股','龙头',
 '最大','生态链','全资子公司','世界500强','行业领先','股份','通讯','汽车股份','hk','sh','sz','300','600','002']
MID_SIGNALS = ['有限公司','科技','专精特新','子公司','领投','融资','研发制造','智能','新能源']
def capacity_tier(fullname, biz):
    t = (fullname or '') + ' ' + (biz or '')
    tl = t.lower()
    big = sum(1 for s in BIG_SIGNALS if s.lower() in tl)
    mid = sum(1 for s in MID_SIGNALS if s.lower() in tl)
    if big >= 2: return 3
    if big >= 1 or mid >= 2: return 2
    return 1
CAP_CEIL_CNY = {3: 5_0000_0000, 2: 5000_0000, 1: 1000_0000}   # 软上限(CNY)

# ================================================================ 1) 企业
def load_companies():
    wb = openpyxl.load_workbook(XLSX, data_only=True); ws = wb.active
    hdr = [c.value for c in ws[1]]; ix = {h: i for i, h in enumerate(hdr)}
    comps = []
    for ri in range(2, ws.max_row + 1):
        row = [ws.cell(row=ri, column=ci + 1).value for ci in range(len(hdr))]
        short = row[ix['公司简称']]
        if not short: continue
        ind = row[ix['六大行业']]
        dim = IND2DIM.get(ind, 'None')
        imp_raw = row[ix['公司重要程度']]
        rank = IMP_RANK.get((imp_raw or '').strip(), 1) if imp_raw else 1
        biz = (row[ix['主营业务']] or '').strip()
        full = (row[ix['公司全称']] or '').strip()
        biztab = (row[ix['企业业务表']] or '')
        ctx = ' '.join(str(x) for x in [biz, full, biztab, row[ix['十项服务']] or ''])
        subs = company_subcats(ctx)
        if not subs:
            subs = DIM_DEFAULT_SUB.get(dim, [])[:3]  # 回退到维度缺省子类目
            sub_source = 'dim_default'
        else:
            sub_source = 'matched'
        cap = capacity_tier(full, biz)
        comps.append(dict(
            idx=len(comps), short=short, full=full, industry=ind, dim=dim,
            domestic=row[ix['境内/境外']], country=row[ix['所在国别']],
            importance=IMP_LABEL[rank], rank=rank,
            biz=biz[:700], subcats=subs, sub_source=sub_source,
            capacity_tier=cap, value_ceiling_cny=CAP_CEIL_CNY[cap],
            channel=row[ix['对接渠道细分']], status=row[ix['业务状态']],
            biztab=str(biztab)[:300] if biztab else '',
        ))
    wb.close()
    return comps

# ================================================================ 2) 招标池
def load_bids():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; cur = con.cursor()
    # 翻译缓存
    trans = {r[0]: r[1] for r in cur.execute("SELECT source_text,translated FROM translation_cache")}
    # 机构画像 tier
    orgtier = {r[0]: r[1] for r in cur.execute("SELECT cnpj,tier FROM orgaos")}
    rows = cur.execute("""SELECT pncp_id, dimension_primary, objeto_compra, valor_cny_estimado,
        valor_total_estimado, valor_total_homologado, data_encerramento_proposta, modalidade_nome,
        srp, is_long_term_opportunity, uf_sigla, municipio_nome, orgao_razao_social, orgao_cnpj,
        link_sistema_origem, modo_disputa_nome
        FROM contratacoes WHERE date(data_encerramento_proposta)>=date('2026-06-09')""").fetchall()
    bids = []
    for r in rows:
        obj = r['objeto_compra'] or ''
        try:
            dl = datetime.datetime.fromisoformat(str(r['data_encerramento_proposta'])).date()
            dias = (dl - TODAY).days
        except Exception:
            dl = None; dias = None
        feas = feasibility(obj)
        if feas == 'infeasible':
            continue  # 直接剔除纯本地工程/人力服务
        subs = bid_subcats(obj)
        srp = bool(r['srp']); lt = bool(r['is_long_term_opportunity'])
        mod = r['modalidade_nome'] or ''
        is_creden = 'credenciamento' in mod.lower()
        # 时效渠道band: near(<30 已本地化才投), mid(30-90), long(>90/长期/credenciamento/SRP)
        if is_creden or lt or (dias is not None and dias >= 90):
            tband = 'long'
        elif dias is not None and dias >= 30:
            tband = 'mid'
        else:
            tband = 'near'
        bids.append(dict(
            pncp_id=r['pncp_id'], dim=r['dimension_primary'] or 'None',
            objeto_pt=obj[:240], objeto_zh=(trans.get(obj) or '')[:160],
            valor_cny=round(r['valor_cny_estimado'] or 0, 0),
            valor_brl=round(r['valor_total_estimado'] or 0, 0),
            deadline=str(dl) if dl else None, dias=dias,
            modalidade=mod, srp=srp, is_long_term=lt, is_creden=is_creden,
            uf=r['uf_sigla'], municipio=r['municipio_nome'],
            organ=r['orgao_razao_social'], organ_tier=orgtier.get(r['orgao_cnpj'], ''),
            feas=feas, subcats=subs, tband=tband,
            link=r['link_sistema_origem'],
        ))
    con.close()
    return bids

# ================================================================ 3) PCA 未来管道
def load_pca():
    con = sqlite3.connect(DB); cur = con.cursor()
    # 按维度近似: 用类别+pdm关键词归类到子类目，聚合金额/机构
    rows = cur.execute("""SELECT categoria_item_pca_nome, descricao_item, valor_total,
        orgao_entidade_razao_social, data_desejada FROM pca_itens WHERE ano_pca>=2026
        AND categoria_item_pca_nome IN ('Material','Soluções de TIC')""").fetchall()
    pca = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0, collections.Counter()]))
    for cat, desc, val, org, dd in rows:
        subs = bid_subcats(desc or '')
        if not subs: continue
        for s in subs:
            # 找子类目所属维度
            for dim, deflist in DIM_DEFAULT_SUB.items():
                if s in deflist:
                    rec = pca[dim][s]
                    rec[0] += (val or 0); rec[1] += 1
                    if org: rec[2][org] += 1
    out = {}
    for dim, subm in pca.items():
        items = []
        for s, (tot, n, orgs) in sorted(subm.items(), key=lambda x: -x[1][0]):
            items.append(dict(subcat=s, total_brl=round(tot, 0), n_items=n,
                              top_orgs=[o for o, _ in orgs.most_common(5)]))
        out[dim] = items
    con.close()
    return out

# ================================================================ 4) 候选生成
def gen_candidates(comps, bids):
    # 按 subcat 与 dim 建索引
    by_sub = collections.defaultdict(list)
    by_dim = collections.defaultdict(list)
    for b in bids:
        for s in b['subcats']:
            by_sub[s].append(b)
        by_dim[b['dim']].append(b)
    stats = {}
    for c in comps:
        seen = set(); pool = []
        # 1) 子类目直配（跨维度）
        for s in c['subcats']:
            for b in by_sub.get(s, []):
                if b['pncp_id'] in seen: continue
                seen.add(b['pncp_id'])
                ov = set(b['subcats']) & set(c['subcats'])
                pool.append((b, 2 if ov else 1, ov))
        # 2) 同维度补充（子类目未覆盖到的同维度可行标）
        for b in by_dim.get(c['dim'], []):
            if b['pncp_id'] in seen: continue
            seen.add(b['pncp_id'])
            pool.append((b, 0.5, set()))
        # 评分: 子类目权重 + 可行性 + 金额匹配 + 机构tier + 轻微偏好长窗口(给未本地化留路)
        ceil = c['value_ceiling_cny']
        scored = []
        for b, w, ov in pool:
            sc = w * 3
            sc += 1.0 if b['feas'] == 'feasible' else 0.4
            v = b['valor_cny'] or 0
            # 金额匹配作为"加权偏好"而非硬过滤(避免把子类目直配的大单挤出召回, 由Agent判断产能)
            if v <= 0:           vfit = 'undisclosed'; sc += 0.3
            elif v <= ceil:      vfit = 'fit';         sc += 1.0
            elif v <= ceil * 3:  vfit = 'consortium';  sc += 0.4   # 略超→可联合体/分包
            else:                vfit = 'oversized';   sc += 0.0   # 远超产能但保留,Agent判定
            b['value_fit'] = vfit
            if b['organ_tier'] in ('A', 'AA', 'high'): sc += 0.3
            if b['tband'] == 'long':         sc += 0.3       # 未本地化可从容布局
            scored.append((sc, b, sorted(ov)))
        scored.sort(key=lambda x: (-x[0], -(x[1]['valor_cny'] or 0)))
        # 取 top40，但保证三时间轴均有覆盖
        top = scored[:40]
        have = {t[1]['tband'] for t in top}
        for need in ('long', 'mid'):
            if need not in have:
                extra = [x for x in scored[40:] if x[1]['tband'] == need][:6]
                top += extra
        cand = []
        for sc, b, ov in top:
            d = dict(b); d['match_subcats'] = ov; d['score'] = round(sc, 2)
            # 候选文件里压缩字段
            cand.append({k: d[k] for k in ('pncp_id','dim','objeto_zh','objeto_pt','valor_cny',
                'valor_brl','deadline','dias','modalidade','srp','is_creden','is_long_term',
                'uf','organ','organ_tier','feas','subcats','tband','match_subcats','value_fit','score','link')})
        rec = dict(company={k: c[k] for k in ('idx','short','full','industry','dim','importance',
                    'rank','biz','subcats','sub_source','capacity_tier','value_ceiling_cny',
                    'channel','status','biztab')},
                   n_candidates=len(cand), candidates=cand)
        with open(os.path.join(CAND, f"c{c['idx']:04d}.json"), 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        stats[c['idx']] = len(cand)
    return stats

# ================================================================ main
def main():
    comps = load_companies()
    bids  = load_bids()
    pca   = load_pca()
    # 维度池
    by_dim = collections.defaultdict(list)
    for b in bids: by_dim[b['dim']].append(b)
    for dim, lst in by_dim.items():
        lst2 = sorted(lst, key=lambda b: -(b['valor_cny'] or 0))
        with open(os.path.join(OUT, f"pool_{dim}.json"), 'w', encoding='utf-8') as f:
            json.dump(lst2, f, ensure_ascii=False, indent=1)
    for dim, items in pca.items():
        with open(os.path.join(OUT, f"pca_{dim}.json"), 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
    cstats = gen_candidates(comps, bids)
    with open(os.path.join(OUT, 'companies.json'), 'w', encoding='utf-8') as f:
        json.dump(comps, f, ensure_ascii=False, indent=1)
    # manifest（给工作流）: 按重要度排序
    manifest = []
    for c in sorted(comps, key=lambda x: (-x['rank'], x['dim'])):
        tier = ('T1_high' if c['rank'] == 4 else 'T2_mid' if c['rank'] == 3
                else 'T3_low' if c['rank'] == 2 else 'T4_none')
        manifest.append(dict(idx=c['idx'], short=c['short'], dim=c['dim'],
            importance=c['importance'], rank=c['rank'], tier=tier,
            n_cand=cstats.get(c['idx'], 0),
            candfile=os.path.join(CAND, f"c{c['idx']:04d}.json").replace('\\', '/')))
    with open(os.path.join(OUT, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    # 统计
    fc = collections.Counter(b['feas'] for b in bids)
    dc = collections.Counter(b['dim'] for b in bids)
    cand_empty = [m['short'] for m in manifest if m['n_cand'] == 0]
    stats = dict(
        n_companies=len(comps), n_bids_after_feas_filter=len(bids),
        feas_breakdown=dict(fc), bids_by_dim=dict(dc),
        companies_by_importance=dict(collections.Counter(c['importance'] for c in comps)),
        companies_by_dim=dict(collections.Counter(c['dim'] for c in comps)),
        cand_count_min=min(cstats.values()), cand_count_max=max(cstats.values()),
        cand_count_avg=round(sum(cstats.values()) / len(cstats), 1),
        companies_with_zero_candidates=cand_empty,
        sub_source_dim_default=sum(1 for c in comps if c['sub_source'] == 'dim_default'),
        pca_dims={k: len(v) for k, v in pca.items()},
    )
    with open(os.path.join(OUT, 'prepare_stats.json'), 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
