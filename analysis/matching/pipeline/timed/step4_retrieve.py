# -*- coding: utf-8 -*-
"""Step4: 候选竞标召回(关键词命中 + 维度匹配 -> 综合预打分 -> top-K)。"""
import sys, io, json, math
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from match_lib import OUT, kw_hits_pt, kw_hits_zh, norm_industry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

TOPK = 22
MINCAND = 12   # 强制覆盖:每家至少这么多候选(不足用泛货物填充),保证可出 top-10

companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
tenders   = json.loads((OUT/"tenders_clean.json").read_text("utf-8"))
print(f"企业 {len(companies)} | 画像 {len(profiles)} | 招标池 {len(tenders)}")

# 预备 tender 索引
for t in tenders:
    t["_dias"] = t.get("dias")
    t["_val"] = t.get("valor_wan")  # 万元;None/0=保密


def value_fit(val_wan, cap_wan, sigilo):
    if sigilo or val_wan is None or val_wan == 0:
        return 0.55   # 预算保密:中性偏可
    if cap_wan is None or cap_wan <= 0:
        cap_wan = 1000
    if val_wan > cap_wan * 3:
        return 0.03   # 远超产能:基本不可行
    if val_wan > cap_wan:
        return 0.25   # 略超:需联合体
    # 落在产能内:越接近 cap*0.4 越甜(既有体量又有余力)
    sweet = cap_wan * 0.4
    ratio = val_wan / sweet if sweet > 0 else 1
    return max(0.35, math.exp(-((math.log(ratio)) ** 2) / 1.2))


def timeliness_fit(dias, srp, lt, loc_status, loc_need):
    """需本地化企业偏好 SRP/长期/远期;已在巴西企业可吃近端标。返回(分, 渠道标签)。"""
    open_ended = dias is None
    can_direct = (loc_status == "已在巴西")
    if can_direct:
        if open_ended or srp or lt:
            return 0.9, "直投/挂网"
        if dias is not None and 3 <= dias <= 90:
            return 0.85, "直投"
        if dias is not None and dias < 3:
            return 0.4, "直投(窗口紧)"
        return 0.7, "直投"
    # 需本地化:近端无法直投,偏向管线/挂网/联合体
    if open_ended or lt:
        return 0.95, "本地化后挂网(长期)"
    if srp:
        return 0.8, "本地化后入围SRP框架"
    if dias is not None and dias > 60:
        return 0.7, "争取本地化后直投"
    if dias is not None and dias > 25:
        return 0.45, "联合体/本地代理"
    return 0.2, "仅联合体/下一轮卡位"


def lead_days(loc_status, loc_need):
    """企业从今天起到『能够投标/挂网』所需的最短本地化+备标周期(天)。"""
    if loc_status == "已在巴西":
        return 14          # 已有CNPJ,仅备标
    if loc_status == "葡语区(较易)":
        return 90
    return {"低": 90, "中": 150, "高": 240}.get(loc_need, 240)   # 需本地化


def time_feasible(dias, lead):
    """截止前能否完成本地化+备标:无截止(credenciamento常年)恒可;否则 dias>=lead。"""
    if dias is None:
        return True
    return dias >= lead


candidates = {}
stats = {"no_profile":0, "no_cand":0, "ok":0, "用泛填充":0}
for cid, c in companies.items():
    p = profiles.get(cid)
    if not p or p.get("_error"):
        stats["no_profile"] += 1
        candidates[cid] = []
        continue
    kws_pt = [str(x) for x in (p.get("keywords_pt") or []) if x]
    kws_zh = [str(x) for x in (p.get("keywords_zh") or []) if x]
    cap = p.get("value_cap_wan_cny")
    try:
        cap = float(cap) if cap is not None else None
    except (TypeError, ValueError):
        cap = None
    # 行业:主表缺失(无行业企业)时用画像推断行业
    industry = norm_industry(c.get("行业")) or norm_industry(p.get("inferred_industry"))
    loc_status = c.get("本地化状态")
    loc_need = p.get("localization_need")

    # 召回分两条通道:
    #   A) 关键词命中(真实产品对得上)—— 主力,关键词相关性主导排序
    #   B) 同维度但 0 命中的 *货物* 标 —— 仅作填充,且排除明显服务/credenciamento
    kw_cands, filler = [], []
    for t in tenders:
        ph = kw_hits_pt(t.get("obj_pt"), kws_pt)
        zh = kw_hits_zh(t.get("obj_zh"), kws_zh)
        n_hits = len(set(ph)) + len(set(zh))
        same_dim = bool(t.get("dim") and t["dim"] == industry)
        otype = t.get("obj_type")
        vf = value_fit(t.get("_val"), cap, t.get("sigilo"))
        tf, channel = timeliness_fit(t.get("_dias"), t.get("srp_bool"), t.get("lt_bool"), loc_status, loc_need)
        conf = float(t.get("dim_conf") or 0.5)
        type_pen = {"goods":0.0, "service":-0.25, "event":-0.30}.get(otype, 0.0)
        srp_lt_bonus = (0.10 if t.get("srp_bool") else 0) + (0.12 if t.get("lt_bool") else 0)

        if n_hits > 0:
            # 关键词相关性主导(0.55..1.0),可行性/时效作次级调节
            rel = 0.5 + 0.1 * min(n_hits, 5)
            if same_dim:
                rel = min(1.0, rel + 0.05)
            pre = rel*0.60 + vf*0.16 + tf*0.14 + conf*0.04 + srp_lt_bonus + type_pen
            kw_cands.append((pre, channel, n_hits, sorted(set(ph)), sorted(set(zh)), t))
        elif same_dim and otype == "goods":
            # 同维度货物填充(无产品命中):明显服务/挂号征召不要
            modal = str(t.get("modalidade") or "")
            if "Credenciamento" in modal:
                continue
            pre = 0.30*0.60 + vf*0.16 + tf*0.14 + conf*0.04 + srp_lt_bonus + type_pen
            filler.append((pre, channel, 0, [], [], t))

    kw_cands.sort(key=lambda x: -x[0])
    filler.sort(key=lambda x: -x[0])
    top = kw_cands[:TOPK]
    if len(top) < TOPK:
        top += filler[:TOPK - len(top)]
    # 强制覆盖:仍不足 MINCAND → 用泛货物标(按价值契合+时效)填充,保证可出 top-10
    if len(top) < MINCAND:
        have = {x[5].get("pncp_id") for x in top}
        generic = []
        for t in tenders:
            if t.get("obj_type") != "goods" or t.get("pncp_id") in have:
                continue
            vf = value_fit(t.get("_val"), cap, t.get("sigilo"))
            tf, channel = timeliness_fit(t.get("_dias"), t.get("srp_bool"), t.get("lt_bool"), loc_status, loc_need)
            srp_lt = (0.10 if t.get("srp_bool") else 0) + (0.12 if t.get("lt_bool") else 0)
            pre = 0.15*0.60 + vf*0.16 + tf*0.14 + srp_lt   # 低相关性基线
            generic.append((pre, channel, 0, [], [], t))
        generic.sort(key=lambda x: -x[0])
        top += generic[:MINCAND - len(top)]
        stats["用泛填充"] += 1

    # ★ 保证不漏掉任何『时效可行』(本地化前能赶上)的产品对口标:全量补入(池子很小)
    L = lead_days(loc_status, loc_need)
    have = {x[5].get("pncp_id") for x in top}
    tf_extra = []
    for t in tenders:
        if t.get("pncp_id") in have or t.get("obj_type") == "works":
            continue
        if not time_feasible(t.get("_dias"), L):
            continue
        ph = kw_hits_pt(t.get("obj_pt"), kws_pt); zh = kw_hits_zh(t.get("obj_zh"), kws_zh)
        n_hits = len(set(ph)) + len(set(zh))
        same_dim = bool(t.get("dim") and t["dim"] == industry)
        if n_hits == 0 and not same_dim:
            continue   # 必须产品对口或同行业
        vf = value_fit(t.get("_val"), cap, t.get("sigilo"))
        rel = (0.5 + 0.1*min(n_hits,5)) if n_hits else 0.30
        pre = rel*0.60 + vf*0.16 + 0.9*0.14 + 0.10   # 时效给高权重(本就可行)
        tf_extra.append((pre, "时效可行", n_hits, sorted(set(ph)), sorted(set(zh)), t))
    tf_extra.sort(key=lambda x: -x[0])
    top += tf_extra[:20]   # 池子小,最多补20个时效可行候选

    if not top:
        stats["no_cand"] += 1
        candidates[cid] = []
        continue
    candidates[cid] = [{
        "pre_score": round(s[0], 4), "channel_hint": s[1], "kw_n": s[2],
        "hit_pt": s[3], "hit_zh": s[4],
        **{k: s[5].get(k) for k in ("pncp_id","obj_zh","obj_pt","modalidade","dim","dim_raw",
            "valor_wan","sigilo","orgao","esfera","uf","deadline","dias","prazo_status",
            "srp_bool","lt_bool","obj_type","link")},
    } for s in top]
    stats["ok"] += 1

(OUT/"candidates.json").write_text(json.dumps(candidates, ensure_ascii=False), encoding="utf-8")
ncand = [len(v) for v in candidates.values() if v]
print("召回完成:", stats)
print(f"有候选企业: {len(ncand)} | 平均候选数: {sum(ncand)/max(len(ncand),1):.1f} | 0候选企业: {stats['no_cand']}")
# 抽样
for cid in list(candidates)[:2]:
    c = companies[cid]; cs = candidates[cid]
    print(f"\n== {c['公司简称']} ({c['行业']},{c['重要程度']}) top3 ==")
    for x in cs[:3]:
        print(f"  [{x['pre_score']}] {x['channel_hint']} | {str(x['obj_zh'])[:40]} | {x['valor_wan']}万 | 剩{x['dias']}天 | hits{x['kw_n']}")
