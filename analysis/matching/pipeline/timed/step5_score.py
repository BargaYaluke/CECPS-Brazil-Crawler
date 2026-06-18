# -*- coding: utf-8 -*-
"""Step5: DeepSeek-v4-flash 对每家企业的候选竞标打分(契合/可行/时效)。"""
import sys, io, json, asyncio, math
import httpx
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import OUT, deepseek_json, run_pool

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
candidates = json.loads((OUT/"candidates.json").read_text("utf-8"))

SYSTEM = (
    "你是中国企业出海巴西、参与巴西政府采购(PNCP)竞标的资深匹配评分专家。"
    "给你一家中国企业的画像,和它的若干候选政府采购标的。请为【每一个】候选标打分。"
    "判分铁律:\n"
    "1) 契合度:标的所采购的货物/服务必须与企业【主营产品】对得上才高分;只是同行业大类但产品对不上,给低分。\n"
    "2) 可行性:小企业不能承接远超其产能/价值上限的大额标;金额远超企业 value_cap 的,可行性必须打低分(<=3)。"
    "本地工程、本地人力服务类中企无法承接,给低分。\n"
    "3) 时效:'需本地化'的企业在巴西没有 CNPJ,无法直接投近端截止(剩余天数少)的标,只能走联合体/本地代理或等本地化后参与;"
    "因此对'需本地化'企业:剩余天数很少的普通标 时效分要低,而 价格登记(SRP框架)/长期机会(credenciamento常年挂网)/无截止/远期 的标 时效分可高(本地化后可入围或滚动供货)。"
    "'已在巴西'的企业可直接投近端标,时效分可高。\n"
    "4) 需求对齐:企业画像里给出了【出海需求】(企业自述诉求)及其对齐度判断。"
    "若企业的出海需求方向与『作为供应商向巴西政府售卖货物』明显不符——例如诉求是从巴西采购/进口商品原料、"
    "引进巴西技术、投资并购非供货资产、仅考察学习交流、融资等——则该企业所有候选 fit 必须<=3 且 recommend=false;"
    "需求为空/未知/宽泛的不惩罚,按主营业务正常判;需求与供货出海一致的正常打分。\n"
    "5) 能力匹配(优先级高于关键词契合,防误杀):先据【梗概动词,而非标题物品名】判断该标真正需要的核心履约能力 core_capability,择一:"
    "纯货物供应 / 工程总包(改造/扩建/翻新/施工/土建/obra/reforma/construção)/ 设备运维(维护/保养/维修/校准/备件/指定品牌/manutenção)/ 持证人力(须医生/护士/技师/兽医/操作员实施)/ 本地常驻劳务(保洁/安保/餐饮/外包人力/mão de obra)/ 持牌网络运营(vale/卡/credenciamento/受理网/商户/封闭支付)/ 本地配送 / 系统集成本地运营 / 咨询设计培训 / 租赁特许长期运营 / 信息不足。"
    "obj_type 仅弱先验(本集多被标 goods,常误标工程/维保/运营),以梗概动词为准。比对企业主营定 capability_gap,并按【可否经本地授权/合伙补足】分流(不与时效重复惩罚):"
    "① 【不可补足】高壁垒类(工程总包/持证人力/本地常驻劳务/持牌网络运营/租赁特许)企业未明确具备 → 名实不符,fit<=3 且 feasibility<=3、recommend=false,risk 写『实为X、缺Y能力』;"
    "② 【可补足】类(设备运维/系统集成本地运营/本地配送)企业是该领域货物/产品商但缺本地授权/技师/网络 → 不强砍 fit(产品确相关),改 feasibility<=4、channel 写『须取得品牌授权或与本地授权服务商/合伙履约』,由 feas 门槛自然过滤;"
    "③ 含备件/耗材/药品的维保或工程标、或供货仅附带而主导是维保/施工/诊疗/运营 → 按主导能力判,fit<=4,不因含采购抬分。"
    "防误杀(默认无罪):纯货物采购标(采购/供应/aquisição/fornecimento+实物,无施工/运维/人力/运营动词)一律 core_capability=纯货物供应、capability_gap=『无』、按产品契合正常打分,严禁因标题敏感词(医院/打印机/支付/学校)臆测降分;信息不足时 capability_gap=『未知』、不当错配硬杀。capability_gap 须能从梗概动词举证,否则填『未知』。\n"
    "严格只输出一个 JSON 对象 {\"items\":[...]},items 内每个候选一个对象,字段:\n"
    '{"idx":候选编号(整数,对应输入), "fit":0-10, "feasibility":0-10, "timeliness":0-10, '
    '"core_capability":"<=14字 该标核心能力要求(上述类别择一)", '
    '"capability_gap":"<=20字 企业缺的核心能力;具备/纯货物对口填『无』,信息不足填『未知』", '
    '"timeliness_label":"直投|本地化后挂网|入围SRP框架|联合体/本地代理|下一轮卡位|不可行", '
    '"channel":"<=30字 落地渠道与动作建议", "reason":"<=50字 为什么契合(扣到具体产品)", '
    '"risk":"<=35字 关键风险", "recommend":true/false}\n'
    "recommend=true 仅当 fit>=6 且 feasibility>=5 且 timeliness>=4 且 capability_gap=『无』。items 长度必须等于候选数。"
)


def build_user(c, p, cands):
    lines = [
        "【企业画像】",
        f"名称: {c.get('公司简称')} / {c.get('公司全称')}",
        f"行业: {c.get('行业')} | 重要程度: {c.get('重要程度')} | 本地化状态: {c.get('本地化状态')}",
        f"主营概括: {p.get('biz_summary')}",
        f"产品线: {p.get('product_lines')}",
        f"产能档位: {p.get('capacity_tier')} | 单标价值上限(万元): {p.get('value_cap_wan_cny')} | 本地化难度: {p.get('localization_need')}",
        f"政采契合: {p.get('gov_fit')} | 是否实物供应商: {p.get('is_goods_supplier')}",
        f"出海需求(企业自述): {(str(c.get('出海需求')).strip() if c.get('出海需求') and not isinstance(c.get('出海需求'), float) else '(未提供)')[:500]}",
        f"需求对齐度(画像判断): {p.get('need_alignment') or '未知'} | 说明: {p.get('need_note') or ''}",
        "",
        "【候选标的】(逐条打分,idx 对应序号)",
    ]
    def _isnan(v):
        return v is None or (isinstance(v, float) and math.isnan(v))
    for i, x in enumerate(cands):
        if x.get("sigilo"):
            val = "预算保密"
        elif _isnan(x.get("valor_wan")):
            val = "金额未披露"
        else:
            val = f"{x.get('valor_wan')}万元"
        dias = "无截止" if _isnan(x.get("dias")) else f"剩{int(x['dias'])}天"
        flags = []
        if x.get("srp_bool"): flags.append("SRP价格登记")
        if x.get("lt_bool"): flags.append("长期挂网")
        flag = ("|"+",".join(flags)) if flags else ""
        lines.append(
            f"[{i}] 维度={x.get('dim')} 类型={x.get('obj_type')} 金额={val} {dias} "
            f"级别={x.get('esfera')} 州={x.get('uf')} 方式={x.get('modalidade')}{flag}\n"
            f"     标的: {str(x.get('obj_zh') or x.get('obj_pt'))[:160]}"
        )
    return "\n".join(lines)


async def main():
    todo = [(cid, companies[cid], profiles.get(cid), candidates[cid])
            for cid in candidates if candidates[cid] and profiles.get(cid) and not profiles[cid].get("_error")]
    print(f"待评分企业: {len(todo)}")
    async with httpx.AsyncClient() as client:
        factories = [
            (lambda sem, c=c, p=p, cs=cs: deepseek_json(client, SYSTEM, build_user(c,p,cs), sem=sem))
            for (_, c, p, cs) in todo
        ]
        outs = await run_pool(factories, concurrency=10)

    results = {}
    n_ok = 0; n_items = 0; mism = 0
    for (cid, c, p, cs), o in zip(todo, outs):
        items = (o or {}).get("items") if isinstance(o, dict) else (o if isinstance(o, list) else None)
        if not items:
            results[cid] = {"_error": True}
            continue
        by_idx = {}
        for it in items:
            try:
                k = int(it.get("idx"))
            except (TypeError, ValueError):
                continue
            if 0 <= k < len(cs):
                by_idx[k] = it
        if len(by_idx) != len(cs):
            mism += 1
        merged = []
        for i, x in enumerate(cs):
            it = by_idx.get(i, {})
            def num(v, d=0.0):
                try: return float(v)
                except (TypeError, ValueError): return d
            fit, feas, tim = num(it.get("fit")), num(it.get("feasibility")), num(it.get("timeliness"))
            final = round(0.45*fit + 0.30*feas + 0.25*tim, 3)
            gap = it.get("capability_gap")
            ok_cap = (not gap) or gap == "无"     # 无缺口/未填=不阻断;『未知』或具体缺口=阻断 recommend
            merged.append({**x,
                "fit":fit, "feasibility":feas, "timeliness":tim, "final_score":final,
                "core_capability":it.get("core_capability"), "capability_gap":gap,
                "timeliness_label":it.get("timeliness_label"), "channel":it.get("channel"),
                "reason":it.get("reason"), "risk":it.get("risk"),
                "recommend":bool(it.get("recommend")) and fit>=6 and feas>=5 and tim>=4 and ok_cap,
            })
        merged.sort(key=lambda z: -z["final_score"])
        results[cid] = {"scored": merged}
        n_ok += 1; n_items += len(merged)
    (OUT/"scored.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(f"评分成功 {n_ok}/{len(todo)} | 候选条目 {n_items} | 长度不符(已按idx对齐) {mism}")
    rec = sum(1 for v in results.values() if v.get("scored") for z in v["scored"] if z["recommend"])
    print("recommend=true 条目总数:", rec)

asyncio.run(main())
