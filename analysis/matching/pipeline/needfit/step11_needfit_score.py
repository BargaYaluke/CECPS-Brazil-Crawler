# -*- coding: utf-8 -*-
"""Step11(需求匹配版): DeepSeek-v4-flash 纯需求契合打分 —— 只评 fit,不评时效/产能/本地化。"""
import sys, io, json, asyncio
import httpx
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import OUT, deepseek_json, run_pool

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
candidates = json.loads((OUT/"candidates_needfit.json").read_text("utf-8"))

SYSTEM = (
    "你是中国企业出海巴西、对接巴西政府采购(PNCP)需求的资深匹配分析师。"
    "本轮匹配【只看需求契合】:不考虑投标截止/时效(过期与否无关)、不考虑本地化可行性、不考虑企业产能与金额承接力。"
    "给你一家中国企业的画像(主营产品/业务+出海需求),和若干候选政府采购标的,为【每一个】候选打分。\n"
    "判分规则:\n"
    "1) fit(0-10) = 标的所需的货物/服务/工程 与 企业主营产品/业务 的实质契合度:"
    "具体产品/服务能直接对上=8-10;基本对口但需小幅适配=6-7;同行业大类但具体对不上=3-5;不相干=0-2。"
    "标的是服务/工程时,企业业务本身是该类服务/工程的提供商才算契合;货物商配服务标、服务商配货物标都算错配给低分。\n"
    "2) 需求对齐:企业【出海需求】方向须与『向巴西(政府)供应该类货物/服务』一致才能高分;"
    "若诉求是从巴西采购/进口、引进巴西技术/资产、投资并购非供货、仅考察交流、纯融资,则该企业所有候选 fit 必须<=3。"
    "需求为空/未知/宽泛不惩罚,按主营业务判断;诉求含拓巴西市场/出口/找订单渠道/在巴西设公司建厂=对齐。\n"
    "3) 能力匹配(优先级高于关键词契合,防误杀):先据【梗概文本里的动词,而非标题物品名】判断该标真正需要的核心履约能力 core_capability,择一:"
    "纯货物供应 / 工程总包(改造/扩建/翻新/施工/土建/obra/reforma/construção/instalação predial)/ 设备运维(维护/保养/维修/校准/备件/指定品牌/原厂/manutenção)/ 持证人力(须医生/护士/技师/兽医/操作员亲自实施)/ 本地常驻劳务(保洁/安保/门卫/餐饮/外包人力/limpeza/vigilância/mão de obra)/ 持牌网络运营(vale/卡/credenciamento/受理网/商户/对账/封闭支付)/ 本地配送 / 系统集成本地运营 / 咨询设计培训 / 租赁特许长期运营 / 信息不足。"
    "obj_type 仅作弱先验(本集标的多被标 goods,常把工程/维保/运营误标 goods),以梗概动词为准。"
    "再据企业主营/产品线/是否实物供应商比对,定 capability_gap:"
    "① 高壁垒【不可补足】类(工程总包/持证人力/本地常驻劳务/持牌网络运营/租赁特许)企业未明确具备 → 名实不符,fit 强制<=3、recommend=false,reason 须点明『实为X、需Y能力』;"
    "② 【可补足】类(设备运维/系统集成本地运营/本地配送)企业是该领域货物/产品商但缺本地授权/技师/网络 → fit 封顶 4-5(情报位);"
    "③ 含备件/耗材/药品(com peças)的维保或工程标、或供货仅附带而主导是维保/施工/诊疗/运营 → 按主导能力判,fit<=4,不因含采购抬分。"
    "防误杀(默认无罪):纯货物采购标(采购/供应/aquisição/fornecimento/compra de + 实物,且无施工/运维/人力/运营动词)一律 core_capability=纯货物供应、capability_gap=『无』、按产品契合正常高分,严禁因标题敏感词(医院/打印机/支付/学校)臆测降分;梗概信息不足无法判定时 capability_gap=『未知』、fit<=5,不当错配硬杀。capability_gap 必须能从梗概动词举证,举不出则填『未知』。\n"
    "严格只输出一个 JSON 对象 {\"items\":[...]},每个候选一个对象:\n"
    '{"idx":候选编号(整数), "fit":0-10, "type_match":"货物|服务|工程|混合", '
    '"core_capability":"<=14字 该标核心能力要求(上述类别择一)", '
    '"capability_gap":"<=20字 企业缺的核心能力;具备/纯货物对口填『无』,信息不足填『未知』", '
    '"reason":"<=50字 为什么契合/不契合(扣到具体产品或业务、能力)", '
    '"action":"<=30字 对接建议(不谈时效与本地化)", "recommend":true/false}\n'
    "recommend=true 仅当 fit>=6 且 capability_gap=『无』。items 长度必须等于候选数。"
)


def build_user(c, p, cands):
    need = c.get("出海需求")
    need = (str(need).strip()[:500] if need and not isinstance(need, float) else "(未提供)")
    lines = [
        "【企业画像】",
        f"名称: {c.get('公司简称')} / {c.get('公司全称')}",
        f"行业: {c.get('行业')}",
        f"主营概括: {p.get('biz_summary')}",
        f"产品线: {p.get('product_lines')}",
        f"是否实物供应商: {p.get('is_goods_supplier')}",
        f"出海需求(企业自述): {need}",
        f"需求对齐度(画像判断): {p.get('need_alignment') or '未知'} | 说明: {p.get('need_note') or ''}",
        "",
        "【候选标的】(逐条打分,idx 对应序号;截止时间一律不影响打分)",
    ]
    for i, x in enumerate(cands):
        lines.append(
            f"[{i}] 维度={x.get('dim')} 类型={x.get('obj_type')}\n"
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
    n_ok = n_items = mism = 0
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
            try:
                fit = float(it.get("fit"))
            except (TypeError, ValueError):
                fit = 0.0
            gap = it.get("capability_gap")
            ok_cap = (not gap) or gap == "无"     # 无缺口/未填=不阻断;『未知』或具体缺口=阻断 recommend
            merged.append({**x, "fit": fit, "type_match": it.get("type_match"),
                           "core_capability": it.get("core_capability"), "capability_gap": gap,
                           "reason": it.get("reason"), "action": it.get("action"),
                           "recommend": bool(it.get("recommend")) and fit >= 6 and ok_cap})
        merged.sort(key=lambda z: -z["fit"])
        results[cid] = {"scored": merged}
        n_ok += 1; n_items += len(merged)
    (OUT/"scored_needfit.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    rec = sum(1 for v in results.values() if v.get("scored") for z in v["scored"] if z["recommend"])
    print(f"评分成功 {n_ok}/{len(todo)} | 条目 {n_items} | 长度不符 {mism} | recommend=true: {rec}")

asyncio.run(main())
