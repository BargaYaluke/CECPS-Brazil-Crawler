# -*- coding: utf-8 -*-
"""Step6: 按重要程度分配 —— 优质标的「首推」软独占给最重要企业。"""
import sys, io, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from match_lib import OUT

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

companies = {c["cid"]: c for c in json.loads((OUT/"company_master.json").read_text("utf-8"))}
profiles  = json.loads((OUT/"company_profiles.json").read_text("utf-8"))
scored    = json.loads((OUT/"scored.json").read_text("utf-8"))
# 对抗校验裁决(高/中企业本轮可竞标匹配):剔除→不进推荐表;降级/通过→保留并由 step7 标注
verdicts  = json.loads((OUT/"verdicts.json").read_text("utf-8")) if (OUT/"verdicts.json").exists() else {}
def _rejected(cid, z):
    return verdicts.get(f"{cid}|{z.get('pncp_id')}", {}).get("结论") == "剔除"
rejected_rows = []

QUOTA = {1:8, 2:6, 3:4, 4:3}     # 每家推荐标的数(按重要程度)

def _basic(c: dict) -> dict:
    return {"cid": c["cid"], "重要程度": c["重要程度"], "公司简称": c["公司简称"],
            "公司全称": c.get("公司全称"), "行业": c.get("行业"), "本地化状态": c.get("本地化状态")}

# 1) 每家企业取「推荐」候选(recommend=True);不足则放宽到 fit>=5 的次优作备选
per_company = {}
near_miss = {}   # cid -> [高契合但本轮时效不可行的标],作为本地化后/下一轮捕获情报
for cid, v in scored.items():
    if not v or v.get("_error"):
        continue
    lst = v.get("scored") or []
    c = companies[cid]
    # 记录被对抗校验剔除的(便于审计),并从候选中剔除
    for z in lst:
        if _rejected(cid, z):
            rejected_rows.append({
                "重要程度": c["重要程度"], "imp_rank": c["imp_rank"], "公司简称": c["公司简称"],
                "行业": c["行业"], "竞标中文梗概": z.get("obj_zh") or z.get("obj_pt"),
                "金额万CNY": ("预算保密" if z.get("sigilo") else z.get("valor_wan")),
                "契合分": z.get("fit"), "可行性分": z.get("feasibility"), "时效分": z.get("timeliness"),
                "校验说明": verdicts.get(f"{cid}|{z.get('pncp_id')}", {}).get("说明"),
                "PNCP编号": z.get("pncp_id"),
            })
    rec = [z for z in lst if z.get("recommend") and not _rejected(cid, z)]
    rec.sort(key=lambda z: -z["final_score"])
    q = QUOTA[c["imp_rank"]]
    chosen = rec[:max(q, 0)]
    # 不足配额:用 fit>=5 且 *时效可行(tim>=4)* 的次优补为「备选」—— 不把"不可行"标当推荐
    if len(chosen) < q:
        pool = [z for z in lst if (not z.get("recommend")) and not _rejected(cid, z)
                and z["fit"] >= 5 and z["feasibility"] >= 4 and z["timeliness"] >= 4]
        pool.sort(key=lambda z: -z["final_score"])
        for z in pool[: q - len(chosen)]:
            z = dict(z); z["_backup"] = True
            chosen.append(z)
    per_company[cid] = chosen
    # 近端高契合「待捕获」:产品高度契合(fit>=7)但因本地化未完成/近端截止本轮时效不可行(tim<4)
    near_miss[cid] = [z for z in lst if z["fit"] >= 7 and z["timeliness"] < 4]

# 2) 首推软独占:按 (重要程度↑, 该企业最高分↓) 处理,优质标的首推给最重要企业
order = sorted(
    per_company.keys(),
    key=lambda cid: (companies[cid]["imp_rank"],
                     -(per_company[cid][0]["final_score"] if per_company[cid] else 0)),
)
claimed = {}     # pncp_id -> cid(已作为某企业首推)
for cid in order:
    chosen = per_company[cid]
    if not chosen:
        continue
    headline = None
    for z in chosen:                      # 取该企业最高分、且未被更重要企业首推占用的标
        if z["pncp_id"] not in claimed:
            headline = z; break
    if headline is None:
        headline = chosen[0]              # 都被占了:与他人共享首推
    headline_pncp = headline["pncp_id"]
    if headline_pncp not in claimed:
        claimed[headline_pncp] = cid
    for z in chosen:
        z["_headline"] = (z["pncp_id"] == headline_pncp)
        if z.get("_headline") and claimed.get(headline_pncp) != cid:
            z["_shared_with"] = companies[claimed[headline_pncp]]["公司简称"]

# 3) 展平成匹配行 + 排序
rows = []
for cid in order:
    c = companies[cid]
    chosen = per_company[cid]
    # 企业内按 首推优先、再 final_score
    chosen = sorted(chosen, key=lambda z: (not z.get("_headline"), -z["final_score"]))
    for prio, z in enumerate(chosen, 1):
        rows.append({
            "cid": cid, "imp_rank": c["imp_rank"], "重要程度": c["重要程度"],
            "公司简称": c["公司简称"], "公司全称": c.get("公司全称"), "行业": c["行业"],
            "本地化状态": c["本地化状态"],
            "推荐优先级": prio, "首推": "★首推" if z.get("_headline") else "",
            "备选": "备选" if z.get("_backup") else "",
            "共享首推": z.get("_shared_with",""),
            "竞标中文梗概": z.get("obj_zh") or z.get("obj_pt"),
            "金额万CNY": ("预算保密" if z.get("sigilo") else z.get("valor_wan")),
            "采购方式": z.get("modalidade"), "标的类型": z.get("obj_type"),
            "截止日期": z.get("deadline"), "剩余天数": z.get("dias"),
            "州": z.get("uf"), "级别": z.get("esfera"), "采购机构": z.get("orgao"),
            "价格登记SRP": "是" if z.get("srp_bool") else "否",
            "长期挂网": "是" if z.get("lt_bool") else "否",
            "契合分": z.get("fit"), "可行性分": z.get("feasibility"), "时效分": z.get("timeliness"),
            "综合分": z.get("final_score"),
            "时效渠道": z.get("timeliness_label"), "落地建议": z.get("channel"),
            "匹配理由": z.get("reason"), "风险": z.get("risk"),
            "PNCP编号": z.get("pncp_id"), "投标链接": z.get("link"),
        })

# 4) 企业总览 + 未匹配名单
overview, unmatched = [], []
for cid, c in companies.items():
    p = profiles.get(cid, {})
    chosen = per_company.get(cid, [])
    n_rec = sum(1 for z in chosen if not z.get("_backup"))
    if not c.get("可匹配"):
        unmatched.append({**_basic(c), "原因": "缺主营业务或行业,无法匹配"})
        continue
    if p.get("_error"):
        unmatched.append({**_basic(c), "原因": "企业画像失败"})
        continue
    nm = near_miss.get(cid, [])
    # 真·未匹配:有效产品契合标=0 且 无近端高契合待捕获
    if not chosen and not nm:
        reason = f"政采契合={p.get('gov_fit')};无对口货物标(多为本地服务/工程或无对应品类)"
        unmatched.append({**_basic(c), "原因": reason, "画像备注": p.get("note")})
        continue
    chs = [z.get("timeliness_label") for z in chosen]
    overview.append({
        **_basic(c),
        "政采契合": p.get("gov_fit"), "是否实物供应商": p.get("is_goods_supplier"),
        "产能档位": p.get("capacity_tier"), "单标上限万元": p.get("value_cap_wan_cny"),
        "本地化难度": p.get("localization_need"),
        "本轮可竞标数": n_rec, "含备选数": max(len(chosen)-n_rec, 0),
        "近端高契合待捕获": len(nm),
        "首推标的": next((z.get("obj_zh") for z in chosen if z.get("_headline")), None),
        "时效渠道分布": {x: chs.count(x) for x in set(chs)},
        "主营概括": p.get("biz_summary"), "落地提示": p.get("note"),
    })

# 5) 高契合·待本地化捕获行(产品高度对口但本轮时效不可投 —— 本地化优先级情报)
nm_rows = []
for cid in order:
    c = companies[cid]
    nm = sorted(near_miss.get(cid, []), key=lambda z: -z["fit"])[:6]
    for z in nm:
        nm_rows.append({
            "重要程度": c["重要程度"], "imp_rank": c["imp_rank"], "公司简称": c["公司简称"],
            "公司全称": c.get("公司全称"), "行业": c["行业"], "本地化状态": c["本地化状态"],
            "竞标中文梗概": z.get("obj_zh") or z.get("obj_pt"),
            "金额万CNY": ("预算保密" if z.get("sigilo") else z.get("valor_wan")),
            "采购方式": z.get("modalidade"), "截止日期": z.get("deadline"), "剩余天数": z.get("dias"),
            "州": z.get("uf"), "级别": z.get("esfera"),
            "价格登记SRP": "是" if z.get("srp_bool") else "否",
            "长期挂网": "是" if z.get("lt_bool") else "否",
            "契合分": z.get("fit"), "可行性分": z.get("feasibility"), "时效分": z.get("timeliness"),
            "本轮不可投原因": z.get("risk") or "本地化未完成/近端截止",
            "捕获建议": z.get("channel") or "加速CNPJ/认证,入围SRP或credenciamento捕获下一周期",
            "匹配理由": z.get("reason"),
            "PNCP编号": z.get("pncp_id"), "投标链接": z.get("link"),
        })

def _dump(name, obj):
    (OUT/name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

_dump("matches.json", rows)
_dump("company_overview.json", overview)
_dump("unmatched.json", unmatched)
_dump("near_miss.json", nm_rows)
_dump("rejected.json", rejected_rows)
print(f"本轮可竞标行 {len(rows)} | 总览企业 {len(overview)} | 高契合待捕获行 {len(nm_rows)} | 真未匹配 {len(unmatched)} | 校验剔除 {len(rejected_rows)}")
print("首推(独占)数:", len(claimed))
import collections
bytier = collections.Counter(companies[r["cid"]]["重要程度"] for r in rows)
nmtier = collections.Counter(r["重要程度"] for r in nm_rows)
print("本轮可竞标·各重要程度行数:", dict(bytier))
print("待捕获·各重要程度行数:", dict(nmtier))
