# -*- coding: utf-8 -*-
"""按 PNCP编号 取每个标的标项级 tipoBeneficio(ME/EPP 专属/预留/无),聚合成标级标签。"""
import sys, io, json, re, time
from collections import Counter
import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REF = "analysis/matching/outputs/pncp_links_refetch.json"
OUT = "analysis/matching/outputs/meepp_benefit.json"
ITEMS = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens"
HEAD = {"User-Agent": "Mozilla/5.0 (compatible; pncp-check/1.0)"}

def parse(s):
    m = re.match(r"^(\d{14})-(\d+)-(\d+)/(\d{4})$", str(s).strip())
    if not m: return None
    c, _x, seq, ano = m.groups(); return c, ano, str(int(seq))

def label(counts, n):
    excl = counts.get("Participação exclusiva para ME/EPP", 0)
    resv = counts.get("Cota reservada para ME/EPP", 0)
    sub = counts.get("Subcontratação para ME/EPP", 0)
    if n and excl == n:
        return "ME/EPP专属(全部标项)", True
    if excl:
        return f"部分标项ME/EPP专属({excl}/{n})", True   # 大企业被排除在这些标项外
    if resv:
        return f"含ME/EPP预留份额({resv}/{n},大企业可投主份额)", False
    if sub:
        return "要求向ME/EPP分包", False
    return "无ME/EPP限制", False

ref = json.load(open(REF, encoding="utf-8"))
pncps = list(ref.keys())
out = {}
with httpx.Client(timeout=45, headers=HEAD, follow_redirects=True) as cli:
    for i, pn in enumerate(pncps):
        p = parse(pn)
        rec = {"pncp": pn, "n_items": 0, "benefit_counts": {}, "label": None,
               "exclui_grande": None, "err": None}
        if not p:
            rec["err"] = "格式错误"; out[pn] = rec; continue
        c, ano, seq = p
        url = ITEMS.format(cnpj=c, ano=ano, seq=seq)
        items = None
        for attempt in range(5):
            try:
                r = cli.get(url)
                if r.status_code == 200 and r.text.strip():
                    items = r.json(); break
                rec["err"] = f"HTTP {r.status_code}/空({len(r.content)}B)"
            except Exception as e:
                rec["err"] = f"{type(e).__name__}"
            time.sleep(2 + attempt * 1.5)
        if items is not None:
            cnt = Counter(it.get("tipoBeneficioNome") for it in items)
            rec["n_items"] = len(items)
            rec["benefit_counts"] = dict(cnt)
            rec["label"], rec["exclui_grande"] = label(cnt, len(items))
            rec["err"] = None
        out[pn] = rec
        time.sleep(0.8)
        if (i + 1) % 16 == 0:
            print(f"  ...{i+1}/{len(pncps)}")

json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
ok = sum(1 for v in out.values() if v["err"] is None)
print(f"\n成功 {ok}/{len(pncps)}")
print("标级 ME/EPP 标签分布:")
for k, n in Counter(v["label"] for v in out.values()).most_common():
    print(f"  {n:3}  {k}")
excl = [v["pncp"] for v in out.values() if v.get("exclui_grande")]
print(f"\n排除大企业的标(专属) 共 {len(excl)} 个 (唯一PNCP):")
for p in excl: print("  ", p)
