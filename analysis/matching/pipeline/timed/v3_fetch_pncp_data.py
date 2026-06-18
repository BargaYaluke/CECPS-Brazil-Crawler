# -*- coding: utf-8 -*-
"""v3: 为新三分类结果(cat1/2/3)补抓 PNCP 数据,与旧缓存合并:
   ① consulta API -> linkSistemaOrigem/situacao/portal (pncp_links_refetch_v3.json)
   ② itens API   -> ME/EPP tipoBeneficio 标签        (meepp_benefit_v3.json)
   ③ 源站+门户连通性探测                              (link_connectivity_v3.json)
   仅对旧缓存没有的 PNCP 发请求(API 限流,慢速+退避;空体当可重试)。"""
import sys, io, json, re, time
from collections import Counter
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from match_lib import OUT  # 共享库(pipeline/),提供绝对 outputs 路径

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
O = str(OUT)  # 原硬编码相对路径 "analysis/matching/outputs" -> 改用 match_lib.OUT,消除 cwd 依赖
API = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
ITEMS = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens"
PORTAL = "https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"
HEAD = {"User-Agent": "Mozilla/5.0 (compatible; pncp-check/1.0)"}

def parse(s):
    m = re.match(r"^(\d{14})-(\d+)-(\d+)/(\d{4})$", str(s).strip())
    if not m:
        return None
    c, _x, seq, ano = m.groups()
    return c, ano, str(int(seq))

# ── 目标 PNCP 集 = cat1 ∪ cat2 ∪ cat3 ──
pncps, links_in = [], {}
for f, key in (("cat1.json", "link"), ("cat2.json", "link"), ("cat3.json", None)):
    for r in json.load(open(f"{O}/{f}", encoding="utf-8")):
        p = r.get("pncp")
        if p and p not in links_in:
            pncps.append(p)
            links_in[p] = r.get("link") if key else None

old_ref = json.load(open(f"{O}/pncp_links_refetch.json", encoding="utf-8"))
old_me = json.load(open(f"{O}/meepp_benefit.json", encoding="utf-8"))
old_conn = json.load(open(f"{O}/link_connectivity.json", encoding="utf-8"))

ref = {p: old_ref[p] for p in pncps if p in old_ref}
me = {p: old_me[p] for p in pncps if p in old_me and old_me[p].get("err") is None}
conn = {p: old_conn[p] for p in pncps if p in old_conn}
todo_ref = [p for p in pncps if p not in ref]
todo_me = [p for p in pncps if p not in me]
print(f"目标 {len(pncps)} 标 | 链接复用 {len(ref)} 补抓 {len(todo_ref)} | ME/EPP复用 {len(me)} 补抓 {len(todo_me)}")

def meepp_label(cnt, n):
    excl = cnt.get("Participação exclusiva para ME/EPP", 0)
    resv = cnt.get("Cota reservada para ME/EPP", 0)
    sub = cnt.get("Subcontratação para ME/EPP", 0)
    if n and excl == n:
        return "ME/EPP专属(全部标项)", True
    if excl:
        return f"部分标项ME/EPP专属({excl}/{n})", True
    if resv:
        return f"含ME/EPP预留份额({resv}/{n},大企业可投主份额)", False
    if sub:
        return "要求向ME/EPP分包", False
    return "无ME/EPP限制", False

with httpx.Client(timeout=45, headers=HEAD, follow_redirects=True) as cli:
    for i, pn in enumerate(todo_ref):
        pa = parse(pn)
        rec = {"pncp": pn, "公司": None, "orig_link": links_in.get(pn), "api_ok": False,
               "linkSistemaOrigem": None, "situacao": None, "modalidade": None,
               "encerramento": None, "portal": None, "err": None}
        if not pa:
            rec["err"] = "PNCP编号格式无法解析"; ref[pn] = rec; continue
        c, ano, seq = pa
        rec["portal"] = PORTAL.format(cnpj=c, ano=ano, seq=seq)
        for attempt in range(5):
            try:
                r = cli.get(API.format(cnpj=c, ano=ano, seq=seq))
                if r.status_code == 200 and r.text.strip():
                    d = r.json()
                    rec.update(api_ok=True, linkSistemaOrigem=d.get("linkSistemaOrigem"),
                               situacao=d.get("situacaoCompraNome"), modalidade=d.get("modalidadeNome"),
                               encerramento=d.get("dataEncerramentoProposta"), err=None)
                    break
                rec["err"] = f"HTTP {r.status_code}/空体"
            except Exception as e:
                rec["err"] = type(e).__name__
            time.sleep(2 + attempt * 1.5)
        ref[pn] = rec
        time.sleep(0.8)
        if (i + 1) % 10 == 0:
            print(f"  [links] {i+1}/{len(todo_ref)}")

    for i, pn in enumerate(todo_me):
        pa = parse(pn)
        rec = {"pncp": pn, "n_items": 0, "benefit_counts": {}, "label": None,
               "exclui_grande": None, "err": None}
        if not pa:
            rec["err"] = "格式错误"; me[pn] = rec; continue
        c, ano, seq = pa
        items = None
        for attempt in range(5):
            try:
                r = cli.get(ITEMS.format(cnpj=c, ano=ano, seq=seq))
                if r.status_code == 200 and r.text.strip():
                    items = r.json(); break
                rec["err"] = f"HTTP {r.status_code}/空({len(r.content)}B)"
            except Exception as e:
                rec["err"] = type(e).__name__
            time.sleep(2 + attempt * 1.5)
        if items is not None:
            cnt = Counter(it.get("tipoBeneficioNome") for it in items)
            rec["n_items"] = len(items)
            rec["benefit_counts"] = dict(cnt)
            rec["label"], rec["exclui_grande"] = meepp_label(cnt, len(items))
            rec["err"] = None
        me[pn] = rec
        time.sleep(0.8)
        if (i + 1) % 10 == 0:
            print(f"  [meepp] {i+1}/{len(todo_me)}")

# ── 连通性:仅探测 conn 里还没有的 ──
def classify(status, err, final_url):
    if err:
        return "无法连接", err
    if status == 200:
        return "HTTP可达(200)", final_url
    if status in (301, 302, 303, 307, 308):
        return f"重定向({status})", final_url
    if status == 403:
        return "被拒/反爬(403)", final_url
    if status in (404, 410):
        return "失效/不存在", final_url
    if 500 <= status < 600:
        return f"服务器错误({status})", final_url
    return f"其他({status})", final_url

todo_conn = [p for p in pncps if p not in conn]
print(f"连通性复用 {len(conn)} 补测 {len(todo_conn)}")
BHEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
         "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}
with httpx.Client(timeout=18, headers=BHEAD, follow_redirects=True, verify=False) as cli:
    def probe(url):
        if not url or not str(url).strip():
            return {"status": None, "final_url": None, "len": None, "err": "无链接"}
        if not str(url).lower().startswith(("http://", "https://")):
            return {"status": None, "final_url": None, "len": None, "err": "链接格式无效(非http,源数据残缺)"}
        last = None
        for attempt in range(3):
            try:
                r = cli.get(url)
                return {"status": r.status_code, "final_url": str(r.url), "len": len(r.content), "err": None}
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:80]}"
                time.sleep(1.5 + attempt)
        return {"status": None, "final_url": None, "len": None, "err": last}
    for i, pn in enumerate(todo_conn):
        rec = ref.get(pn, {})
        eff = rec.get("orig_link") or rec.get("linkSistemaOrigem")
        o = probe(eff)
        p = probe(rec.get("portal"))
        cat, _ = classify(o["status"], o["err"], o["final_url"])
        conn[pn] = {"pncp": pn, "公司": rec.get("公司"), "eff_link": eff,
                    "eff_source": ("原表" if rec.get("orig_link") else ("PNCP补" if rec.get("linkSistemaOrigem") else "无")),
                    "situacao": rec.get("situacao"), "origin_status": o["status"], "origin_len": o["len"],
                    "origin_err": o["err"], "origin_class": cat,
                    "portal": rec.get("portal"), "portal_status": p["status"], "portal_err": p["err"]}
        if (i + 1) % 10 == 0:
            print(f"  [conn] {i+1}/{len(todo_conn)}")

json.dump(ref, open(f"{O}/pncp_links_refetch_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(me, open(f"{O}/meepp_benefit_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(conn, open(f"{O}/link_connectivity_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
api_ok = sum(1 for v in ref.values() if v.get("api_ok"))
me_ok = sum(1 for v in me.values() if v.get("err") is None)
print(f"\n完成: 链接 api_ok {api_ok}/{len(ref)} | ME/EPP 成功 {me_ok}/{len(me)} | 连通性 {len(conn)}")
print("ME/EPP 标签分布:")
for k, n in Counter(v.get("label") for v in me.values()).most_common():
    print(f"  {n:3}  {k}")
