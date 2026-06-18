# -*- coding: utf-8 -*-
"""测试每个标的『有效投标链接』(原链接 or PNCP补的源系统链接)与 PNCP 官方门户链接的可访问性。
   说明:很多巴西采购平台是 SPA / 反爬 / 需登录,HTTP 200 不等于页面真能看到标,
   非 200 也不一定是死链——故输出按"HTTP 可达性"分类并标注局限。"""
import sys, io, json, asyncio
import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = "analysis/matching/outputs/pncp_links_refetch.json"
OUT = "analysis/matching/outputs/link_connectivity.json"
HEAD = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

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

async def probe(cli, url):
    if not url or not str(url).strip():
        return {"status": None, "final_url": None, "len": None, "err": "无链接"}
    if not str(url).lower().startswith(("http://", "https://")):
        return {"status": None, "final_url": None, "len": None, "err": "链接格式无效(非http,源数据残缺)"}
    last = None
    for attempt in range(3):
        try:
            r = await cli.get(url)
            return {"status": r.status_code, "final_url": str(r.url),
                    "len": len(r.content), "err": None}
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:80]}"
            await asyncio.sleep(1.5 + attempt)
    return {"status": None, "final_url": None, "len": None, "err": last}

async def main():
    data = json.load(open(SRC, encoding="utf-8"))
    items = list(data.values())
    sem = asyncio.Semaphore(5)
    results = {}
    async with httpx.AsyncClient(timeout=18, headers=HEAD, follow_redirects=True, verify=False) as cli:
        async def work(rec):
            eff = rec.get("orig_link") or rec.get("linkSistemaOrigem")
            async with sem:
                o = await probe(cli, eff)
                p = await probe(cli, rec.get("portal"))
            cat, detail = classify(o["status"], o["err"], o["final_url"])
            results[rec["pncp"]] = {
                "pncp": rec["pncp"], "公司": rec.get("公司"),
                "eff_link": eff, "eff_source": ("原表" if rec.get("orig_link") else ("PNCP补" if rec.get("linkSistemaOrigem") else "无")),
                "situacao": rec.get("situacao"),
                "origin_status": o["status"], "origin_len": o["len"], "origin_err": o["err"],
                "origin_class": cat,
                "portal": rec.get("portal"), "portal_status": p["status"], "portal_err": p["err"],
            }
        await asyncio.gather(*[work(r) for r in items])

    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    from collections import Counter
    print("=== 有效投标链接(源系统)可达性分类 ===")
    for k, n in Counter(v["origin_class"] for v in results.values()).most_common():
        print(f"  {n:3}  {k}")
    print("\n=== PNCP 官方门户链接 可达性 ===")
    for k, n in Counter((v["portal_status"] or v["portal_err"]) for v in results.values()).most_common():
        print(f"  {n:3}  {k}")
    print("\n=== 链接来源 ===")
    for k, n in Counter(v["eff_source"] for v in results.values()).most_common():
        print(f"  {n:3}  {k}")
    nolink = [v for v in results.values() if v["eff_source"] == "无"]
    print(f"\n仍完全无源系统链接(只能用PNCP门户): {len(nolink)}")

asyncio.run(main())
