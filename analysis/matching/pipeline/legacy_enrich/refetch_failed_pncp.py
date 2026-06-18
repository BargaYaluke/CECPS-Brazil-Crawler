# -*- coding: utf-8 -*-
"""重取 PNCP API 批量时因限流失败(空响应/断连)的标,逐个慢速重试,合并回结果 JSON。"""
import sys, io, json, re, time
import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
JF = "analysis/matching/outputs/pncp_links_refetch.json"
API = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
HEAD = {"User-Agent": "Mozilla/5.0 (compatible; pncp-check/1.0)"}

def parse(s):
    m = re.match(r"^(\d{14})-(\d+)-(\d+)/(\d{4})$", str(s).strip())
    if not m: return None
    c, _x, seq, ano = m.groups(); return c, ano, str(int(seq))

d = json.load(open(JF, encoding="utf-8"))
failed = [k for k, v in d.items() if not v["api_ok"]]
print(f"待重取: {len(failed)}")

def fetch_one(cli, pn):
    p = parse(pn)
    if not p: return None, "格式错误"
    c, ano, seq = p
    url = API.format(cnpj=c, ano=ano, seq=seq)
    last = None
    for attempt in range(6):
        try:
            r = cli.get(url)
            if r.status_code == 200 and r.text.strip():
                return r.json(), None
            last = f"HTTP {r.status_code} / 空响应({len(r.content)}B)"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(2 + attempt * 1.5)  # 退避
    return None, last

fixed = 0
with httpx.Client(timeout=45, headers=HEAD, follow_redirects=True) as cli:
    for i, pn in enumerate(failed):
        js, err = fetch_one(cli, pn)
        rec = d[pn]
        if js:
            rec["api_ok"] = True
            rec["linkSistemaOrigem"] = js.get("linkSistemaOrigem")
            rec["situacao"] = js.get("situacaoCompraNome")
            rec["modalidade"] = js.get("modalidadeNome")
            rec["encerramento"] = js.get("dataEncerramentoProposta")
            rec["err"] = None
            fixed += 1
        else:
            rec["err"] = err
        print(f"  [{i+1}/{len(failed)}] {pn}: {'OK src='+str(rec['linkSistemaOrigem']) if js else 'FAIL '+str(err)}")
        time.sleep(1.0)  # 慢速礼貌

json.dump(d, open(JF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
api_ok = sum(1 for v in d.values() if v["api_ok"])
has_src = sum(1 for v in d.values() if v["linkSistemaOrigem"])
print(f"\n本次修复 {fixed}/{len(failed)} | 现 API成功 {api_ok}/{len(d)} | 有源系统链接 {has_src}")
still = [k for k, v in d.items() if not v["api_ok"]]
if still: print("仍失败:", still)
