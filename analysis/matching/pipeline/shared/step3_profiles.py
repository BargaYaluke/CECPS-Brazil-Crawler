# -*- coding: utf-8 -*-
"""Step3: DeepSeek-v4-flash 企业画像(主营业务 -> 产能/关键词/政采契合)。"""
import sys, io, json, asyncio
import httpx
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import OUT, deepseek_json, run_pool, DEEPSEEK_MODEL

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

companies = json.loads((OUT/"company_master.json").read_text(encoding="utf-8"))

def _s(x):
    """NaN/None 安全取字符串;空/缺失返回 None。"""
    if x is None or isinstance(x, float):
        return None
    s = str(x).strip()
    return s or None

todo = [c for c in companies if c.get("可匹配") and _s(c.get("主营业务")) and _s(c.get("行业"))]
print(f"待画像企业: {len(todo)} / 全部 {len(companies)} (主营/行业缺失的留给 step3b)")

SYSTEM = (
    "你是协助中国企业出海、参与巴西政府采购(PNCP)竞标的资深产业尽调分析师。"
    "根据给定企业的主营业务等信息,判断它能否承接巴西政府的【货物采购】竞标,并产出结构化画像。"
    "巴西政府采购以实物货物(aquisição/fornecimento)为主;本地工程(obra)和本地人力服务中企无法直接承接。"
    "若给出【出海需求】(企业自述的出海诉求),请判断其与『作为供应商参与巴西政府采购竞标、向巴西政府售卖货物』的方向是否一致:"
    "诉求为拓展巴西/拉美市场、出口销售产品、寻找海外订单/渠道/客户等 => 对齐;"
    "诉求为从巴西采购/进口商品原料、引进巴西技术、投资并购非供货资产、仅考察学习交流、融资/上市等与对巴供货无关 => 错配;"
    "诉求宽泛(如泛泛的国际化意向)或与供货部分相关 => 部分对齐;未提供出海需求 => 未知。\n"
    "严格只输出一个 JSON 对象,字段如下,不要任何多余文字:\n"
    "{\n"
    '  "is_goods_supplier": true/false,   // 是否以实物商品/设备/材料供应为主(能投政府货物采购)\n'
    '  "biz_summary": "一句话概括主营(<=30字)",\n'
    '  "product_lines": ["具体产品/品类", ...],   // 3-8个,具体名词\n'
    '  "keywords_zh": ["中文检索词", ...],         // 6-12个,具体品名(用于在中文招标梗概里检索,避免"服务/解决方案"等泛词)\n'
    '  "keywords_pt": ["palavra", ...],          // 6-12个对应葡语巴西政采术语\n'
    '  "capacity_tier": 1,                        // 1小微 2中小 3大型 4超大(龙头/上市/独角兽/全球布局)\n'
    '  "value_cap_wan_cny": 1000,                 // 单个标的可现实承接的金额上限(万元人民币)\n'
    '  "gov_fit": "强|中|弱|几乎无",               // 与巴西政府货物采购总体契合度\n'
    '  "gov_categories": ["医疗医药"],             // 适配的政采品类(可空数组)\n'
    '  "localization_need": "高|中|低",            // 在巴西落地(CNPJ/产品注册/认证/建厂)的难度与耗时\n'
    '  "need_alignment": "对齐|部分对齐|错配|未知",  // 出海需求与"向巴西政府供货竞标"方向的一致性\n'
    '  "need_note": "一句话说明需求对齐判断(<=40字,无出海需求则填\'未提供\')",\n'
    '  "note": "一句关键风险或落地建议(<=40字)"\n'
    "}"
)


def build_user(c: dict) -> str:
    parts = [
        f"公司全称: {c.get('公司全称') or c.get('公司简称')}",
        f"公司简称: {c.get('公司简称')}",
        f"所属六大行业: {c.get('行业')}",
        f"境内/境外: {c.get('境内/境外')}  所在国别: {c.get('所在国别')}  本地化状态: {c.get('本地化状态')}",
        f"主营业务: {(_s(c.get('主营业务')) or '')[:1500]}",
    ]
    if _s(c.get("出海需求")):
        parts.append(f"出海需求(企业自述诉求): {_s(c.get('出海需求'))[:600]}")
    if _s(c.get("十项服务")):
        parts.append(f"诉求/服务: {_s(c.get('十项服务'))[:200]}")
    if _s(c.get("企业拜访表")):
        parts.append(f"近期动态: {_s(c.get('企业拜访表'))[:300]}")
    return "\n".join(parts)


async def main():
    results = {}
    async with httpx.AsyncClient() as client:
        factories = [
            (lambda sem, u=build_user(c): deepseek_json(client, SYSTEM, u, sem=sem))
            for c in todo
        ]
        outs = await run_pool(factories, concurrency=10)
    n_ok = 0
    for c, o in zip(todo, outs):
        if o is None or not isinstance(o, dict):
            results[c["cid"]] = {"_error": True}
            continue
        n_ok += 1
        results[c["cid"]] = o
    (OUT/"company_profiles.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n画像成功 {n_ok}/{len(todo)} (model={DEEPSEEK_MODEL})")
    # 快速质检
    errs = [cid for cid,v in results.items() if v.get("_error")]
    print("失败 cid:", errs[:20], "..." if len(errs)>20 else "")
    fits = {}
    for v in results.values():
        if not v.get("_error"):
            fits[v.get("gov_fit","?")] = fits.get(v.get("gov_fit","?"),0)+1
    print("gov_fit 分布:", fits)
    goods = sum(1 for v in results.values() if v.get("is_goods_supplier"))
    print("is_goods_supplier=True:", goods)

asyncio.run(main())
