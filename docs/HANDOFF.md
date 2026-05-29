# HANDOFF — 会话交接快照

> **用途**:把本文件粘到新 Claude Code 窗口即可快速恢复上下文。
> **分工**:架构 / 分层 / API 接口清单 / 业务规则 → 看 `CLAUDE.md`(单一事实源)。
> 本文件只记两类**易漂移**信息:① 当前运行状态(各表行数等);② CLAUDE.md 尚未收录的坑。
> 最后更新:2026-05-28

---

## 一、项目一句话

爬巴西全国政府采购门户 **PNCP**(`pncp.gov.br`)招投标数据 → 过滤标注 → 六大维度分类 → 导出业务 Excel。
六大维度:数字经济 / 医疗医药 / 高端制造 / 大宗商贸 / 跨境电商 / 文化体育。

进度:**核心闭环全通 + 富化层已落地 + 采集层提速**(采集 7/7 → 存储 7 表 → 过滤 8 规则 → **富化 4 器** → 六维分类 → 7-Sheet Excel),**193 个单元测试全过**。
富化层 = 区域(离线 regions.yaml)/ 汇率(AwesomeAPI BRL→CNY)/ 机构画像(DB 聚合 + BrasilAPI)/ itens 目录类目(Compras.gov.br CATMAT/CATSER)。
**采集提速**:4 个列表接口(publicacao/atualizacao/proposta/contratos)改走 `PncpSession`,默认 `pncp.transport=auto` —— httpx 优先(实测单页 ~4s,浏览器路径要 30s+),撞 F5 自动 fallback 真浏览器。
详细进度与剩余任务见 `CLAUDE.md` §3 / §8。

---

## 二、环境(新窗口必读)

| 项 | 值 |
|---|---|
| Python | `C:\Users\hsm07\miniconda3\envs\brazil_crawler\python.exe`(**必须全路径**;PATH 里只有坏的 WindowsApps stub)|
| 跑命令前 | PowerShell 先 `$env:PYTHONIOENCODING="utf-8"; chcp 65001`(否则中文 GBK 报错)|
| 跑测试 | `python.exe -m pytest`(198 个;2 个真实数据 e2e 在 data/raw 为空时自动 skip)|
| 数据库 | `data/procurement.db`(SQLite)|

> env 信息也存在 auto-memory(`python_env_brazil_crawler.md`),新窗口会自动加载。

---

## 三、当前数据库状态(2026-05-29 实测,已跑 5/22~5/28 一周全量)

| 表 | 行数 | 备注 |
|---|---|---|
| contratacoes | 8,357 | 一周全量;已富化 region/gdp/cny |
| itens | 39,608 | 招标明细 |
| contratos | 35,999 | 一周全量合同 |
| pca_itens | 172,288 | 从 raw 缓存救回(476 个不同计划;`load-pca-cache`)|
| catalogo_compras | 345,103 | CATMAT+CATSER 全量目录 |
| orgaos | 3,526 | `enrich` 聚合画像 |
| sync_cursor | 0 | (fetch-and-store/contratos/pca 不写 cursor,只 atualizacao 写)|

> 报表 `data/exports/report.xlsx` 已生成,7 Sheet 全有数据(PCA 页 17 万行)。

**重新核对行数的命令**(注意:用相对路径,见 §五的路径坑):

```powershell
$code = @'
import sqlite3
c = sqlite3.connect("data/procurement.db"); cur = c.cursor()
for (t,) in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
    print(t, cur.execute("SELECT COUNT(*) FROM " + t).fetchone()[0])
'@
$env:PYTHONIOENCODING = "utf-8"
$code | & "C:\Users\hsm07\miniconda3\envs\brazil_crawler\python.exe" -
```

---

## 四、CLI 命令(全部已实现)

```
fetch-publicacao / fetch-atualizacao / fetch-proposta / fetch-contratos / fetch-pca
fetch-and-store(主表+明细) / fetch-editais(PDF+二次过滤)
fetch-catalogo(CATMAT/CATSER 目录,--tipo material|servico|both)
enrich(区域/汇率/机构画像/itens 目录;开关 --region/--fx/--orgao/--catalogo,默认全跑;--with-brasilapi/--redo)
classify(维度,--redo 回灌) / filter(重跑过滤) / export-excel
```

**业务报告命令链**:
`fetch-atualizacao`(增量抓)→ `fetch-contratos` / `fetch-pca`(竞品+计划)
→ `fetch-editais`(PDF+二次过滤)→ `fetch-catalogo`(目录)→ `enrich`(富化)
→ `classify`(维度)→ `export-excel`(出报告)。

---

## 五、CLAUDE.md 尚未收录的坑(本文件独有)

> 已在 CLAUDE.md 的坑(F5 反爬 / BrowserSession、contratos 与 pca 真实路径、modalidade 必填)看 §3 / §6,这里不重复。

**API 参数实测**:
- `proposta` 的 `dataFinal` 语义是"截止日**上限 ≤**",不是直觉的 ≥。
- `pca` 的日期参数是 `dataInicio` / `dataFim`;**其它接口**都是 `dataInicial` / `dataFinal`。
- `contratos` / `pca` / **Compras.gov.br dadosabertos** 的 `tamanhoPagina` 区间 **10~500**(传更小活体报 400 `Informe um número de paginação no intervalo de 10 a 500`)。
- ⚠️ **但 `publicacao` / `atualizacao` / `proposta`(`/contratacoes/*`)`tamanhoPagina` 上限是 50**(>50 报 400 `Tamanho de página inválido`)—— 跟 contratos/pca **不同**!这 3 个 fetcher 已加 `[10,50]` 自动夹取,`--page-size 500` 会被降到 50。全量命令对这 3 个用默认 50,只有 contratos/pca 才用 `--page-size 500`。

**富化数据源实测(本次)**:
- **BCB PTAX 不含 CNY**:`olinda.bcb.gov.br/.../PTAX` 的开放 OData 只有 10 种主流货币,`CotacaoMoedaPeriodo('CNY')` 返回空 → BRL→CNY 改用 **AwesomeAPI**(`economia.awesomeapi.com.br/json/last/BRL-CNY`,取 `BRLCNY.bid`)。
- **CATSER 同码去重**:`consultarItemServico` 灌 3085 条,按 `(tipo, codigo)` UPSERT 后剩 3000 distinct(85 条 codigoServico 重复)—— 正常,维表要的就是 code→类目 的去重映射。
- **区域富化不爬 IBGE**:UF→大区/GDP 是离线查 `regions.yaml`,不调外部 API。

**导出 / PCA 内存坑(本次)**:
- **导出内存恒定**:`export-excel` 用 openpyxl `write_only` 流式 + 只查必要列(避开每行几 KB 的 `raw_json`)。**旧版 `select(Model)` 加载完整 ORM + 一次性 list + 普通 openpyxl,在 PCA 17 万行时 OOM 卡死整机**;现重构后 26 万行报表峰值仅 ~100MB。改 `exporters/excel.py` 的列定义时,key 必须是真实 ORM 列名(`_stream_columns` 直接 `getattr(model, key)` 去 SELECT)。
- **PCA 逐页提交**:`run_pca` / `load_pca_from_cache` 每 N 头一提交,中断不再回滚全部。raw 缓存(`data/raw/pca/`)和库是两回事 —— 缓存逐页落盘、库靠提交;旧版"全抓完才提交"中断会留下"缓存有、库空"。用 `load-pca-cache` 从缓存救回(不重抓)。

**平台 / 工程坑**:
- **schema 演进**:`create_all` 不会给**已存在**的表加列 → 加字段后旧库报 `no such column`,需手动 ALTER 或删库重建(待上 Alembic)。
- **Windows Chromium 关闭慢**:headless Chromium 进程常 hang → 后台任务跑完常需 `TaskStop`。
- **浏览器导航超时调到 60s**:PNCP 是 SPA 加载慢,30s 不够。
- **非 ASCII 项目路径**:`C:\巴西爬虫` 这种含中文的绝对路径,经 PowerShell 管道 / `python -c` 传入时会乱码,导致 `os.path.exists` 误判。**对策:在项目目录内用相对路径**(如 `data/procurement.db`),或先 `chcp 65001`。

---

## 六、剩余任务(都可选,非阻塞)

| 任务 | 工时 |
|---|---|
| 补 itens 空表(重跑 `fetch-and-store`);补回后 `enrich --catalogo` 富化品类 | ~5 分钟 |
| CATMAT 全量(`fetch-catalogo --tipo material`,34 万条)| 几分钟 |
| Alembic 迁移(解决 schema 演进手动 ALTER)| ~1 小时 |
| 词典调优(降未分类比例,改 `dimension_keywords.yaml`)| 持续 |

**已完成(本次)**:P6 富化层全部落地 —— region/fx/cnpj/catalogo 四模块 + `run_enrichment` + `fetch-catalogo`/`enrich` 命令 + **富化列进 Excel**(招标主表加 CNY金额/大区/GDP分层,新增「机构画像」Sheet → 7-Sheet)+ 23 个新单测。**采集层提速** —— 4 个列表接口改走 `PncpSession`(httpx 优先 / 撞 F5 自动 fallback 浏览器)+ `pncp.transport` 开关 + 7 个新单测。

**明确不做**(用户决定):自动化调度 / 监控告警 / Postgres 迁移 / 其它**招标门户**(ComprasNet listings / BEC …)。注:富化接的 Compras.gov.br 是目录/参考数据,非招标列表。
