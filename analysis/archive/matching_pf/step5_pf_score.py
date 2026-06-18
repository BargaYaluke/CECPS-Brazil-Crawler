# -*- coding: utf-8 -*-
"""Step5(纯契合版): DeepSeek-v4-flash 评分 —— 只评『企业需求/产品 ↔ 标的所需』契合,
   不考虑时效(过期/截止)与本地化可行性(CNPJ/资质/周期/产能)。输出 scored_pf.json。"""
import sys, io, json, asyncio, math
import httpx
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT, deepseek_json, run_pool

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
candidates = json.loads((OUT/"candidates_pf.json").read_text("utf-8"))

SYSTEM = (
    "你是中国企业与巴西政府采购(PNCP)标的的匹配评分专家。"
    "本次评估【完全不考虑】时效因素(标是否过期、截止远近)与本地化可行性(有无CNPJ、资质获取难度、本地化周期、产能规模),"
    "只评『企业的需求与产品』与『标的所采购的货物/服务』之间的实质匹配度。给每个候选标打分。\n"
    "判分铁律:\n"
    "1) 契合度 fit:标的所采购的货物/服务必须与企业【主营产品/核心业务】实质对得上才高分;"
    "只是同行业大类但具体产品/服务对不上,给低分。服务类标对服务/运营企业可以高分(只要业务对口)。\n"
    "2) 需求对齐:若企业的【出海需求】方向与『作为供应商向巴西政府提供其产品/服务』明显不符"
    "——如诉求是从巴西采购/进口商品原料、引进巴西技术/资产、投资并购非供货、仅考察学习交流、纯融资——"
    "则该企业所有候选 fit 必须<=3 且 recommend=false;需求为空/未知/宽泛的不惩罚,按主营业务判断;"
    "诉求含拓巴西市场/出口销售/找订单渠道/在巴西设公司建厂 = 对齐,正常打分。\n"
    "严格只输出一个 JSON 对象 {\"items\":[...]},items 内每个候选一个对象,字段:\n"
    '{"idx":候选编号(整数,对应输入), "fit":0-10, '
    '"reason":"<=50字 为什么契合/不契合(扣到具体产品或服务)", '
    '"risk":"<=35字 主要差距提示(如需本地资质/规模悬殊,仅提示不影响打分)", '
    '"suggestion":"<=30字 若要承接该需求的切入方式", "recommend":true/false}\n'
    "recommend=true 仅当 fit>=6。items 长度必须等于候选数。"
)


def build_user(c, p, cands):
    need = c.get("出海需求")
    need = (str(need).strip() if need and not isinstance(need, float) else "(未提供)")
    lines = [
        "【企业】",
        f"名称: {c.get('公司简称')} / {c.get('公司全称')}",
        f"行业: {c.get('行业')} | 重要程度: {c.get('重要程度')}",
        f"主营概括: {p.get('biz_summary')}",
        f"产品线: {p.get('product_lines')}",
        f"出海需求(企业自述): {need[:500]}",
        f"需求对齐度(画像判断): {p.get('need_alignment') or '未知'} | 说明: {p.get('need_note') or ''}",
        "",
        "【候选标的】(逐条打分,idx 对应序号;金额/级别仅为背景信息)",
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
        lines.append(
            f"[{i}] 维度={x.get('dim')} 类型={x.get('obj_type')} 金额={val} "
            f"级别={x.get('esfera')} 州={x.get('uf')} 方式={x.get('modalidade')}\n"
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
            try:
                fit = float(it.get("fit"))
            except (TypeError, ValueError):
                fit = 0.0
            merged.append({**x,
                "fit": fit, "reason": it.get("reason"), "risk": it.get("risk"),
                "suggestion": it.get("suggestion"),
                "recommend": bool(it.get("recommend")) and fit >= 6,
            })
        merged.sort(key=lambda z: -z["fit"])
        results[cid] = {"scored": merged}
        n_ok += 1; n_items += len(merged)
    (OUT/"scored_pf.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(f"评分成功 {n_ok}/{len(todo)} | 候选条目 {n_items} | 长度不符(已按idx对齐) {mism}")
    rec = sum(1 for v in results.values() if v.get("scored") for z in v["scored"] if z["recommend"])
    print("recommend=true 条目总数:", rec)

asyncio.run(main())
