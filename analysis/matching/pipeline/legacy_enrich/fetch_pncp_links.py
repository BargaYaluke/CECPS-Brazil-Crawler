# -*- coding: utf-8 -*-
"""按 PNCP编号 调 PNCP 官方 consulta API 取每个标的真实 linkSistemaOrigem + 状态,
   并构造稳定的官方门户链接。结果存 JSON 供后续填表/连通性测试。"""
import sys, io, json, re, time
import pandas as pd
import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

TARGET = "data/exports/中葡企业_巴西竞标匹配_三分类主表_v2.xlsx"
OUT = "analysis/matching/outputs/pncp_links_refetch.json"
API = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
PORTAL = "https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"
HEAD = {"User-Agent": "Mozilla/5.0 (compatible; pncp-check/1.0)"}

def parse_pncp(s):
    # 格式: {cnpj}-{x}-{seq}/{ano}  例 31710696000173-1-000002/2026
    m = re.match(r"^(\d{14})-(\d+)-(\d+)/(\d{4})$", str(s).strip())
    if not m:
        return None
    cnpj, _x, seq, ano = m.groups()
    return cnpj, ano, str(int(seq))

m = pd.read_excel(TARGET, dtype=str)
rows = []
for _, r in m.iterrows():
    rows.append({"pncp": str(r["PNCP编号"]), "公司": r.get("公司简称"),
                 "orig_link": (None if pd.isna(r["投标链接"]) else r["投标链接"])})

results = {}
with httpx.Client(timeout=40, headers=HEAD, follow_redirects=True) as cli:
    for i, row in enumerate(rows):
        pn = row["pncp"]; parsed = parse_pncp(pn)
        rec = {"pncp": pn, "公司": row["公司"], "orig_link": row["orig_link"],
               "api_ok": False, "linkSistemaOrigem": None, "situacao": None,
               "modalidade": None, "encerramento": None, "portal": None, "err": None}
        if not parsed:
            rec["err"] = "PNCP编号格式无法解析"; results[pn] = rec; continue
        cnpj, ano, seq = parsed
        rec["portal"] = PORTAL.format(cnpj=cnpj, ano=ano, seq=seq)
        url = API.format(cnpj=cnpj, ano=ano, seq=seq)
        for attempt in range(3):
            try:
                resp = cli.get(url)
                if resp.status_code == 200:
                    d = resp.json()
                    rec["api_ok"] = True
                    rec["linkSistemaOrigem"] = d.get("linkSistemaOrigem")
                    rec["situacao"] = d.get("situacaoCompraNome")
                    rec["modalidade"] = d.get("modalidadeNome")
                    rec["encerramento"] = d.get("dataEncerramentoProposta")
                    break
                else:
                    rec["err"] = f"API HTTP {resp.status_code}"
            except Exception as e:
                rec["err"] = f"{type(e).__name__}: {e}"
                time.sleep(1.5)
        results[pn] = rec
        if (i + 1) % 16 == 0:
            print(f"  ...{i+1}/{len(rows)}")

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)

api_ok = sum(1 for v in results.values() if v["api_ok"])
has_src = sum(1 for v in results.values() if v["linkSistemaOrigem"])
filled = sum(1 for v in results.values() if not v["orig_link"] and v["linkSistemaOrigem"])
still_missing = sum(1 for v in results.values() if not v["orig_link"] and not v["linkSistemaOrigem"])
print(f"\nAPI成功 {api_ok}/{len(rows)} | 有源系统链接 {has_src} | 可补上原本缺失的 {filled} | 仍无源链接 {still_missing}")
print("状态分布:")
from collections import Counter
for k, n in Counter(v["situacao"] for v in results.values()).most_common():
    print(f"  {k}: {n}")
