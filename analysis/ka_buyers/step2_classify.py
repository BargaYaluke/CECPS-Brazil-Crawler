# -*- coding: utf-8 -*-
"""Step2: 给每条 KA 标打 一级行业 / 二级专业领域 / 需求类型(DeepSeek 批量)。

一级行业:从固定表中择一(列清洁可筛选);二级:精炼专业领域(≤8字);需求类型:固定7类。
长期挂网 由 step1 确定性判定,不在此重复。
批量 12 标/调,异步并发,复用 match_lib 的磁盘缓存(改提示词会失效→重新计费)。
产物: outputs/classifications.json  {pncp: {l1, l2, demand_type, reason}}
"""
import sys, io, json, asyncio
from pathlib import Path
import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "matching" / "pipeline"))
from match_lib import deepseek_json, DEEPSEEK_MODEL  # noqa

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = HERE / "outputs"

L1 = ["医疗医药", "工程建设", "食品餐饮", "车辆交通", "信息技术数字化", "办公与后勤",
      "环卫与市政", "教育文体", "农业畜牧", "能源电力", "安防消防", "服装纺织日用",
      "机械设备", "化工材料", "环保水务", "园林绿化", "金融服务", "物流仓储",
      "燃料油气", "建材五金", "其他"]
DEMAND = ["产品采购", "服务外包", "工程总包", "租赁运营", "维修维护", "系统建设", "长期入库/征召", "其他"]

SYSTEM = (
    "你是巴西政府采购行业分类专家。给你一批招标(葡语原文+中文梗概),为每条输出三类标签,只回 JSON。\n"
    "【一级行业】必须严格从此表择一(不得新造,无法归类才用『其他』):\n"
    + "、".join(L1) + "\n"
    "【二级专业领域】≤8字的细分领域,要具体到可指导找供应商。示例:\n"
    "  医疗医药→ 医疗设备/超声诊断/义齿制作/专科诊疗/药品耗材/检验试剂/救护车/医院EPC/口腔正畸\n"
    "  工程建设→ 道路工程/桥梁/排水管网/学校建设/医院建设/楼宇改造/铺装/土方\n"
    "  食品餐饮→ 校餐食品/食材配送/营养餐外包/粮油\n"
    "  车辆交通→ 乘用车/卡车/摩托车/工程机械车/车辆租赁/公交运营\n"
    "  信息技术数字化→ 软件系统/IT集成/数据中心/网络设备/视频监控平台\n"
    "  办公与后勤→ 办公家具/办公耗材/清洁外包/安保外包/餐补卡\n"
    "【需求类型】严格从此7类择一,判断该标真实履约模式(看动词而非名词):\n"
    "  产品采购=买货物(aquisição/fornecimento de bens);服务外包=人力/专业服务(limpeza/vigilância/serviços médicos/mão de obra);\n"
    "  工程总包=土建工程(obras/construção/reforma/pavimentação);租赁运营=租赁或特许经营(locação/concessão);\n"
    "  维修维护=维修保养(manutenção/conserto/assistência técnica);系统建设=信息系统/平台集成(sistema/software/solução);\n"
    "  长期入库/征召=credenciamento 或 注册入库长期供应(chamamento público/registro de preços 持续供货登记)。\n"
    "输出格式: {\"items\":[{\"pncp\":\"...\",\"l1\":\"医疗医药\",\"l2\":\"医疗设备\",\"demand\":\"产品采购\",\"reason\":\"一句话\"}, ...]}\n"
    "items 必须与输入同序、同数量。reason≤20字。"
)


def build_user(batch):
    lines = []
    for b in batch:
        pt = (b.get("obj_pt") or "")[:360]
        zh = (b.get("obj_zh") or "")[:160]
        modal = b.get("modalidade") or ""
        lines.append(f"PNCP={b['pncp']} | 方式={modal} | 中文={zh} | 葡语={pt}")
    return "请分类以下 %d 条:\n" % len(batch) + "\n".join(lines)


async def main():
    bids = json.loads((OUT/"ka_bids.json").read_text("utf-8"))
    BATCH = 12
    batches = [bids[i:i+BATCH] for i in range(0, len(bids), BATCH)]
    print(f"标 {len(bids)} → {len(batches)} 批(每批{BATCH})")
    sem = asyncio.Semaphore(8)
    results = {}
    async with httpx.AsyncClient() as client:
        async def do(bi, batch):
            r = await deepseek_json(client, SYSTEM, build_user(batch), sem=sem)
            return bi, batch, r
        tasks = [asyncio.create_task(do(bi, b)) for bi, b in enumerate(batches)]
        done = 0
        for fut in asyncio.as_completed(tasks):
            bi, batch, r = await fut
            done += 1
            items = (r or {}).get("items") if isinstance(r, dict) else (r if isinstance(r, list) else None)
            if items and len(items) == len(batch):
                for b, it in zip(batch, items):
                    l1 = it.get("l1") if it.get("l1") in L1 else "其他"
                    dem = it.get("demand") if it.get("demand") in DEMAND else "其他"
                    results[b["pncp"]] = {"l1": l1, "l2": (it.get("l2") or "").strip()[:12] or "未细分",
                                          "demand": dem, "reason": (it.get("reason") or "").strip()[:40]}
            else:
                # 兜底:逐条按 pncp 对齐(若模型给了 pncp)
                bymap = {str(x.get("pncp")): x for x in (items or []) if isinstance(x, dict)}
                for b in batch:
                    it = bymap.get(str(b["pncp"]), {})
                    l1 = it.get("l1") if it.get("l1") in L1 else "其他"
                    dem = it.get("demand") if it.get("demand") in DEMAND else "其他"
                    results[b["pncp"]] = {"l1": l1, "l2": (it.get("l2") or "").strip()[:12] or "未细分",
                                          "demand": dem, "reason": (it.get("reason") or "").strip()[:40]}
            if done % 20 == 0 or done == len(batches):
                print(f"  [classify] {done}/{len(batches)} 批", flush=True)
    (OUT/"classifications.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    # 统计
    from collections import Counter
    c1 = Counter(v["l1"] for v in results.values())
    cd = Counter(v["demand"] for v in results.values())
    miss = [b["pncp"] for b in bids if b["pncp"] not in results]
    print(f"\n已写 classifications.json ({len(results)}/{len(bids)});缺 {len(miss)}")
    print("一级行业分布:", dict(c1.most_common()))
    print("需求类型分布:", dict(cd.most_common()))
    # 二级 top
    c2 = Counter(v["l2"] for v in results.values())
    print("二级 top20:", dict(c2.most_common(20)))

asyncio.run(main())
