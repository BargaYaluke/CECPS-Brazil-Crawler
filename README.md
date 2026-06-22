# 巴西政府采购爬虫(lite)

从巴西政府采购平台 **PNCP**(`pncp.gov.br`)抓取全国招标(contratacoes)及其明细(itens),
清洗掉对中企无价值的标,补全背景(金额 BRL→CNY 折算、地区、时效、葡→中翻译),
导出中文 Excel 数据快照,并据此生成「设备/产品采购标」交付表。

项目两部分:
- **爬虫**(`src/`):PNCP → 本地库 `data/procurement.db` → 中文数据快照 `data/exports/report.xlsx`
- **分析**(`analysis/`):设备/产品采购标筛选 —— 见 [analysis/README.md](analysis/README.md)

> **lite 重构(2026-06-18)**:已删合同/PCA/品类目录/机构画像/六维分类/PDF抽取等功能,
> 及企业匹配/KA金主/市场报告三条分析线,只保留「爬招标 → 翻译 → 设备产品表」一条链。

## 环境

Python 3.11(conda 环境 `brazil_crawler`)。每个新的 PowerShell 窗口先设 UTF-8(否则中文乱码):

```powershell
$env:PYTHONIOENCODING="utf-8"; chcp 65001 > $null
cd C:\巴西爬虫
```

DeepSeek 翻译需在 `.env` 配 `DEEPSEEK_API_KEY`。

## 用法

爬虫流水线,按顺序跑(`--start/--end` 按需改;命令**幂等**,中断可直接重跑):

```powershell
python -m src.cli fetch-and-store --start 2026-05-22 --end 2026-05-28   # 抓招标主表 + 明细
python -m src.cli fetch-proposta                               # 还能投的快照(未过期)
python -m src.cli enrich                                       # 富化:金额 BRL→CNY / 地区
python -m src.cli translate                                    # 葡→中翻译(DeepSeek,按原文缓存)
python -m src.cli export-excel --out data/exports/report.xlsx  # 导出数据快照
python analysis/equipment_bids/find_bids.py                    # 生成「设备/产品采购标」
```

**周更一键脚本**(自动跑 增量爬取→还能投快照→富化→翻译→导出 report.xlsx→生成设备产品表):

```powershell
# 首次(空库初始化):抓当前"还能投"的开放标全量
powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -First
# 日常每周(增量更新):以后每周跑这一条
powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1
# 额外出"全量含过期"表
powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -AllMode
```

流水线(两种模式都按此漏斗):**爬取 → 富化(算CNY)→ 筛(未过期 + 20-200万CNY)→ 翻译该集 → 出 `report_<日期>.xlsx` → 实物设备/产品逻辑 → 出 `巴西设备产品采购标_20-200万CNY_<日期>.xlsx`**。

- 爬取**固定按发布日期**(不可调)。**首次** `-First` 走 `fetch-and-store`,抓过去**一个月发布**的标及明细。
- **周更**(无参):①`purge-expired` 删已过期标(截止日<今天)+明细,写 `logs/expired_purged.csv`;②`fetch-atualizacao` 查已有标更新;③`fetch-and-store` 爬上一周发布的标。
- 「筛选」是出表/翻译时的过滤(`--active-only --cny-min 200000 --cny-max 2000000`),不物理删库;翻译只翻这个集省 DeepSeek 费用;设备表用动态当天判未过期。
- **两个产出都带日期戳**(report 与设备表共用同一个 `RUN_STAMP`)。
- 日志:全过程落 `logs/run_<时间戳>.log`;各阶段指标(清理过期/采集/富化/翻译/导出/设备表)落 `logs/pipeline_metrics.csv`。
- `-OpenHorizonDays N` 调开放标快照的未来跨度(默认 30 天)。
- 计划任务示例(每周一 07:00)见 `scripts\run_weekly.ps1` 头部注释;任一步失败脚本返回非 0。

设备/产品采购标的用法见 [analysis/README.md](analysis/README.md)。

## 数据

```
data/
├── procurement.db  本地库(SQLite,contratacoes / itens / translation_cache / sync_cursor)
├── inputs/         人工维护的企业源表(保留,本线不用)
├── exports/        数据快照 report.xlsx + 设备产品采购标(输出)
└── filtered_log/   被过滤记录(审计,可追溯为何被丢)
```
