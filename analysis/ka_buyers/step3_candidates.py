# -*- coding: utf-8 -*-
"""Step3: 荐企海选(DeepSeek 按 (一级,二级) 桶批量生成强出海意愿中企候选池)。

桶: (一级行业, 二级_canon)。某 (一级,二级) 标数<3 的稀有细分 → 归入 (一级,"综合") 桶,
保证每桶名册有意义且桶数可控。每桶喂 主导需求类型 + 3~5 个代表梗概,DeepSeek 出 ~12 家:
  {name, brief(主营), cap(产品/工程/服务/运营/系统/综合), size(大/中/小), overseas(出海线索), fit}
产物: outputs/bucket_index.json(标→桶) + outputs/candidate_pools.json(桶→候选[])
"""
import sys, io, json, re, asyncio
from collections import Counter, defaultdict
from pathlib import Path
import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "matching" / "pipeline"))
from match_lib import deepseek_json  # noqa
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = HERE / "outputs"

bids = json.loads((OUT/"ka_bids.json").read_text("utf-8"))
cls = json.loads((OUT/"classifications.json").read_text("utf-8"))


def canon_l2(s):
    s = (s or "未细分").strip()
    s = re.sub(r"(采购|供应|项目|服务采购)$", "", s) or s
    syn = {"医用耗材": "医疗耗材", "药品耗材": "医疗耗材", "医疗器械": "医疗设备",
           "药品": "药品采购", "普通货物": "综合货物", "一般货物": "综合货物",
           "道路铺装": "道路工程", "路面铺装": "道路工程"}
    return syn.get(s, s) or "未细分"


# 标 → 初始 (一级,二级)
prelim = {}
pair_cnt = Counter()
for b in bids:
    c = cls.get(b["pncp"], {})
    l1 = c.get("l1", "其他")
    l2 = canon_l2(c.get("l2"))
    prelim[b["pncp"]] = (l1, l2)
    pair_cnt[(l1, l2)] += 1

# 稀有 (一级,二级)(<3) → (一级,"综合")
RARE = 3
bucket_of = {}
for pncp, (l1, l2) in prelim.items():
    if pair_cnt[(l1, l2)] < RARE:
        bucket_of[pncp] = (l1, "综合")
    else:
        bucket_of[pncp] = (l1, l2)

buckets = defaultdict(list)
for b in bids:
    buckets[bucket_of[b["pncp"]]].append(b)
print(f"标 {len(bids)} → 桶 {len(buckets)} 个(rare<{RARE}→『综合』)")
sizes = sorted((len(v) for v in buckets.values()), reverse=True)
print("桶大小 top10:", sizes[:10], "| 中位:", sizes[len(sizes)//2], "| 单标桶:", sum(1 for s in sizes if s == 1))

(OUT/"bucket_index.json").write_text(
    json.dumps({p: list(k) for p, k in bucket_of.items()}, ensure_ascii=False), encoding="utf-8")

SYSTEM = (
    "你是『中国企业出海/政府采购』情报专家,熟悉中国各行业有海外拓展动作的企业(含拉美/巴西布局)。\n"
    "给你巴西政府采购的一个细分采购领域(一级/二级)+ 主导需求类型 + 几个真实标的梗概,\n"
    "请列出最适合承接该领域、且【出海意愿强或已有海外动作】的中国企业候选(优先真实存在、上市或行业头部、"
    "有出口/海外项目/海外设厂/参与国际招标记录者;不要编造)。覆盖不同体量(大型龙头+中型专精)。\n"
    "硬约束:① 必须是中国大陆企业;② 与该二级领域真实对口(看履约能力,别只靠名字);"
    "③ 标注其能力类型是否匹配该需求类型;④ 不在『已知国内无出海迹象的纯内贸小厂』之列。\n"
    "每个领域给 10~14 家,只回 JSON:\n"
    '{"companies":[{"name":"全称","brief":"≤14字主营","cap":"产品|工程|服务|运营|系统|综合",'
    '"size":"大|中|小","overseas":"≤18字出海线索(已知的海外项目/出口/设厂,没把握写『需核验』)","fit":"≤16字为何适配该领域"}]}\n'
    "name 用规范全称(便于核验)。宁可少列也不要编造不存在的公司。"
)


def build_user(key, items):
    l1, l2 = key
    dem = Counter(cls.get(b["pncp"], {}).get("demand", "其他") for b in items)
    samples = []
    for b in sorted(items, key=lambda x: -(x.get("valor_wan") or 0))[:5]:
        samples.append(f"- [{b.get('valor_wan')}万CNY] {(b.get('obj_zh') or '')[:70]}")
    return (f"采购领域:一级『{l1}』 / 二级『{l2}』\n"
            f"主导需求类型(标数): {dict(dem.most_common())}\n"
            f"代表标的:\n" + "\n".join(samples) +
            f"\n\n请给出该领域强出海意愿的中国企业候选名册。")


async def main():
    keys = list(buckets.keys())
    sem = asyncio.Semaphore(8)
    pools = {}
    async with httpx.AsyncClient() as client:
        async def do(k):
            r = await deepseek_json(client, SYSTEM, build_user(k, buckets[k]), sem=sem)
            return k, r
        tasks = [asyncio.create_task(do(k)) for k in keys]
        done = 0
        for fut in asyncio.as_completed(tasks):
            k, r = await fut
            done += 1
            comps = (r or {}).get("companies") if isinstance(r, dict) else None
            clean = []
            for c in (comps or []):
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                clean.append({
                    "name": str(c.get("name")).strip()[:40],
                    "brief": str(c.get("brief") or "").strip()[:30],
                    "cap": str(c.get("cap") or "综合").strip()[:8],
                    "size": str(c.get("size") or "中").strip()[:2],
                    "overseas": str(c.get("overseas") or "需核验").strip()[:40],
                    "fit": str(c.get("fit") or "").strip()[:30],
                })
            pools["|".join(k)] = {"l1": k[0], "l2": k[1], "n_bids": len(buckets[k]), "candidates": clean}
            if done % 15 == 0 or done == len(keys):
                print(f"  [pool] {done}/{len(keys)} 桶", flush=True)
    (OUT/"candidate_pools.json").write_text(json.dumps(pools, ensure_ascii=False, indent=1), encoding="utf-8")
    ncand = [len(v["candidates"]) for v in pools.values()]
    empty = [k for k, v in pools.items() if not v["candidates"]]
    print(f"\n已写 candidate_pools.json: {len(pools)} 桶,平均候选 {sum(ncand)/max(len(ncand),1):.1f},空桶 {len(empty)}")
    if empty:
        print("空桶:", empty[:20])

asyncio.run(main())
