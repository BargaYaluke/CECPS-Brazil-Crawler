# -*- coding: utf-8 -*-
"""需求匹配版: 为 cat1/2/3_needfit 的标补抓 PNCP 链接/ME-EPP/连通性(异步并发,~1100标)。
   合并进既有 *_v3.json 缓存(旧 PNCP 全复用,仅新 PNCP 发请求)。
   注: PNCP API 限流(空体=可重试);门户链接纯字符串构造不发请求;门户连通性此前实测全通,不再探测。"""
import sys, io, json, re, asyncio
from collections import Counter
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from match_lib import OUT  # 与 step* 同目录的共享库,提供绝对 outputs 路径

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
O = str(OUT)  # 原硬编码相对路径 "analysis/matching/outputs" -> 改用 match_lib.OUT,消除 cwd 依赖
API = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
ITEMS = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens"
PORTAL = "https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"
HEAD = {"User-Agent": "Mozilla/5.0 (compatible; pncp-check/1.0)"}
BHEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
         "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}

def parse(s):
    m = re.match(r"^(\d{14})-(\d+)-(\d+)/(\d{4})$", str(s).strip())
    if not m: return None
    c, _x, seq, ano = m.groups()
    return c, ano, str(int(seq))

def meepp_label(cnt, n):
    excl = cnt.get("Participação exclusiva para ME/EPP", 0)
    resv = cnt.get("Cota reservada para ME/EPP", 0)
    sub = cnt.get("Subcontratação para ME/EPP", 0)
    if n and excl == n: return "ME/EPP专属(全部标项)", True
    if excl: return f"部分标项ME/EPP专属({excl}/{n})", True
    if resv: return f"含ME/EPP预留份额({resv}/{n},大企业可投主份额)", False
    if sub: return "要求向ME/EPP分包", False
    return "无ME/EPP限制", False

def classify(status, err, final_url):
    if err: return "无法连接"
    if status == 200: return "HTTP可达(200)"
    if status in (301, 302, 303, 307, 308): return f"重定向({status})"
    if status == 403: return "被拒/反爬(403)"
    if status in (404, 410): return "失效/不存在"
    if 500 <= status < 600: return f"服务器错误({status})"
    return f"其他({status})"

def fix_scheme(u):
    if u is None: return None
    u = str(u).strip()
    if not u or u.lower() == "nan": return None
    if u.lower().startswith(("http://", "https://")): return u
    if "." in u.split("/")[0]: return "https://" + u
    return None

pncps, links_in = [], {}
for f in ("cat1_needfit.json", "cat2_needfit.json", "cat3_needfit.json"):
    for r in json.load(open(f"{O}/{f}", encoding="utf-8")):
        p = r.get("pncp")
        if p and p not in links_in:
            pncps.append(p)
            links_in[p] = r.get("link")

ref = json.load(open(f"{O}/pncp_links_refetch_v3.json", encoding="utf-8"))
me = json.load(open(f"{O}/meepp_benefit_v3.json", encoding="utf-8"))
conn = json.load(open(f"{O}/link_connectivity_v3.json", encoding="utf-8"))
todo = [p for p in pncps if p not in ref or p not in me or me.get(p, {}).get("err") is not None
        or p not in conn]
print(f"目标 {len(pncps)} 标 | 待处理 {len(todo)} (其余全缓存复用)")

done_n = 0

async def fetch_json(cli, url, attempts=5):
    for a in range(attempts):
        try:
            r = await cli.get(url)
            if r.status_code == 200 and r.text.strip():
                return r.json(), None
            err = f"HTTP {r.status_code}/空体"
        except Exception as e:
            err = type(e).__name__
        await asyncio.sleep(1.2 + a * 1.5)
    return None, err

async def probe(cli, url):
    if not url:
        return {"status": None, "err": "无链接"}
    if not str(url).lower().startswith(("http://", "https://")):
        return {"status": None, "err": "链接格式无效(非http,源数据残缺)"}
    last = None
    for a in range(2):
        try:
            r = await cli.get(url)
            return {"status": r.status_code, "err": None}
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:60]}"
            await asyncio.sleep(1 + a)
    return {"status": None, "err": last}

async def work(pn, api_cli, web_cli, sem):
    global done_n
    async with sem:
        pa = parse(pn)
        if pn not in ref:
            rec = {"pncp": pn, "公司": None, "orig_link": links_in.get(pn), "api_ok": False,
                   "linkSistemaOrigem": None, "situacao": None, "modalidade": None,
                   "encerramento": None, "portal": None, "err": None}
            if pa:
                c, ano, seq = pa
                rec["portal"] = PORTAL.format(cnpj=c, ano=ano, seq=seq)
                d, err = await fetch_json(api_cli, API.format(cnpj=c, ano=ano, seq=seq))
                if d:
                    rec.update(api_ok=True, linkSistemaOrigem=d.get("linkSistemaOrigem"),
                               situacao=d.get("situacaoCompraNome"), modalidade=d.get("modalidadeNome"),
                               encerramento=d.get("dataEncerramentoProposta"))
                else:
                    rec["err"] = err
            else:
                rec["err"] = "PNCP编号格式无法解析"
            ref[pn] = rec
        if pn not in me or me.get(pn, {}).get("err") is not None:
            rec = {"pncp": pn, "n_items": 0, "benefit_counts": {}, "label": None,
                   "exclui_grande": None, "err": None}
            if pa:
                c, ano, seq = pa
                items, err = await fetch_json(api_cli, ITEMS.format(cnpj=c, ano=ano, seq=seq))
                if items is not None:
                    cnt = Counter(it.get("tipoBeneficioNome") for it in items)
                    rec["n_items"] = len(items)
                    rec["benefit_counts"] = dict(cnt)
                    rec["label"], rec["exclui_grande"] = meepp_label(cnt, len(items))
                else:
                    rec["err"] = err
            else:
                rec["err"] = "格式错误"
            me[pn] = rec
        if pn not in conn:
            r = ref.get(pn, {})
            eff = fix_scheme(r.get("orig_link")) or fix_scheme(r.get("linkSistemaOrigem"))
            if eff:
                o = await probe(web_cli, eff)
                cls = classify(o["status"], o["err"], None)
            else:
                o = {"status": None, "err": "无链接"}
                cls = "无法连接"
            conn[pn] = {"pncp": pn, "公司": r.get("公司"), "eff_link": eff,
                        "eff_source": ("原表" if r.get("orig_link") else ("PNCP补" if r.get("linkSistemaOrigem") else "无")),
                        "situacao": r.get("situacao"), "origin_status": o["status"],
                        "origin_err": o["err"], "origin_class": cls,
                        "portal": r.get("portal"), "portal_status": None, "portal_err": "未探测(门户此前实测全通)"}
        done_n += 1
        if done_n % 50 == 0:
            print(f"  ...{done_n}/{len(todo)}", flush=True)

async def main():
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=40, headers=HEAD, follow_redirects=True) as api_cli, \
               httpx.AsyncClient(timeout=15, headers=BHEAD, follow_redirects=True, verify=False) as web_cli:
        await asyncio.gather(*[work(pn, api_cli, web_cli, sem) for pn in todo])

asyncio.run(main())

json.dump(ref, open(f"{O}/pncp_links_refetch_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(me, open(f"{O}/meepp_benefit_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(conn, open(f"{O}/link_connectivity_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
api_ok = sum(1 for p in pncps if ref.get(p, {}).get("api_ok"))
me_ok = sum(1 for p in pncps if me.get(p, {}).get("err") is None)
print(f"\n完成: 本表标 api_ok {api_ok}/{len(pncps)} | ME/EPP 成功 {me_ok}/{len(pncps)}")
print("本表 ME/EPP 标签分布:")
for k, n in Counter(me[p].get("label") for p in pncps if p in me).most_common():
    print(f"  {n:3}  {k}")
excl = [p for p in pncps if me.get(p, {}).get("exclui_grande")]
print(f"全标项专属(排除大企业)标: {len(excl)}")
