# -*- coding: utf-8 -*-
"""Step3b: 为缺画像的企业(无行业/无主营)补画像 —— 让全部437家都可进入匹配。"""
import sys, io, json, asyncio
import httpx
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import OUT, deepseek_json, run_pool

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

companies = json.loads((OUT/"company_master.json").read_text("utf-8"))
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))

todo = [c for c in companies if c.get("可匹配") and
        (c["cid"] not in profiles or profiles.get(c["cid"], {}).get("_error"))]
print(f"待补画像: {len(todo)}")

SYSTEM = (
    "你是协助中国企业出海、参与巴西政府采购(PNCP)竞标的资深产业尽调分析师。"
    "下面这家企业的行业或主营业务信息可能缺失,请尽量根据【公司名称】及任何可得信息推断它的业务,产出结构化画像。"
    "巴西政府采购以实物货物(aquisição/fornecimento)为主。"
    "若给出【出海需求】(企业自述诉求),请判断其与『作为供应商参与巴西政府采购、向巴西政府售卖货物』方向是否一致:"
    "拓市场/出口销售/找订单渠道=>对齐;从巴西采购进口/引进技术/投资并购非供货/仅考察交流/融资=>错配;"
    "宽泛或部分相关=>部分对齐;未提供=>未知。\n"
    "严格只输出一个 JSON 对象,字段如下,不要任何多余文字:\n"
    "{\n"
    '  "inferred_industry": "高端制造|跨境商贸|医疗医药|数字经济|大宗商贸|文化体育",  // 据公司推断最可能的六大行业\n'
    '  "is_goods_supplier": true/false,\n'
    '  "biz_summary": "一句话概括主营(<=30字)",\n'
    '  "product_lines": ["具体产品/品类", ...],\n'
    '  "keywords_zh": ["中文检索词", ...],   // 6-12个具体品名\n'
    '  "keywords_pt": ["palavra", ...],      // 6-12个对应葡语巴西政采术语\n'
    '  "capacity_tier": 1,                    // 1小微 2中小 3大型 4超大\n'
    '  "value_cap_wan_cny": 1000,\n'
    '  "gov_fit": "强|中|弱|几乎无",\n'
    '  "gov_categories": ["..."],\n'
    '  "localization_need": "高|中|低",\n'
    '  "need_alignment": "对齐|部分对齐|错配|未知",\n'
    '  "need_note": "一句话说明需求对齐判断(<=40字,无出海需求填\'未提供\')",\n'
    '  "note": "一句关键风险或落地建议(<=40字),若信息不足请注明『据公司名推断』"\n'
    "}"
)

def sv(x, default=""):
    """NaN/None-safe string."""
    if x is None:
        return default
    if isinstance(x, float):
        try:
            import math
            if math.isnan(x):
                return default
        except Exception:
            pass
    s = str(x).strip()
    return s if s else default

def build_user(c):
    parts = [
        f"公司全称: {sv(c.get('公司全称'), sv(c.get('公司简称')))}",
        f"公司简称: {sv(c.get('公司简称'))}",
        f"已知六大行业: {sv(c.get('行业'), '(缺失)')}",
        f"境内/境外: {sv(c.get('境内/境外'))}  所在国别: {sv(c.get('所在国别'))}",
        f"主营业务: {sv(c.get('主营业务'), '(缺失,请据公司名推断)')[:1200]}",
    ]
    if sv(c.get("出海需求")):
        parts.append(f"出海需求(企业自述诉求): {sv(c.get('出海需求'))[:600]}")
    for k in ("十项服务","企业拜访表","业务状态","对接渠道细分"):
        if sv(c.get(k)):
            parts.append(f"{k}: {sv(c.get(k))[:200]}")
    return "\n".join(parts)

async def main():
    async with httpx.AsyncClient() as client:
        fs = [(lambda sem, u=build_user(c): deepseek_json(client, SYSTEM, u, sem=sem)) for c in todo]
        outs = await run_pool(fs, concurrency=8)
    n=0
    for c,o in zip(todo,outs):
        if isinstance(o, dict) and not o.get("_error"):
            profiles[c["cid"]] = o; n+=1
        else:
            profiles[c["cid"]] = {"_error": True}
    (OUT/"company_profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"补画像成功 {n}/{len(todo)} | 画像总数 {len(profiles)}")
    for c in todo:
        p = profiles.get(c["cid"], {})
        print(f"   {c['公司简称']}: 行业推断={p.get('inferred_industry')} | {p.get('biz_summary')} | fit={p.get('gov_fit')}")

asyncio.run(main())
