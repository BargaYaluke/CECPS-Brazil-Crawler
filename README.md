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
powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1
# 首跑/补全:回溯过去 N 天发布的标
powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -BackfillDays 60
# 额外出"全量含过期"表
powershell -ExecutionPolicy Bypass -File scripts\run_weekly.ps1 -AllMode
```

- 常规周更走 `fetch-atualizacao` 增量(`sync_cursor` 记上次拉取位点,首跑兜底 today-7d)。
- 计划任务示例(每周一 07:00)见 `scripts\run_weekly.ps1` 头部注释。
- 任一步失败脚本返回非 0(便于计划任务标红)。

设备/产品采购标的用法见 [analysis/README.md](analysis/README.md)。

## 数据

```
data/
├── procurement.db  本地库(SQLite,contratacoes / itens / translation_cache / sync_cursor)
├── inputs/         人工维护的企业源表(保留,本线不用)
├── exports/        数据快照 report.xlsx + 设备产品采购标(输出)
└── filtered_log/   被过滤记录(审计,可追溯为何被丢)
```
