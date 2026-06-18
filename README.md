# 巴西政府采购爬虫与匹配

每天从巴西政府采购平台 **PNCP**(`pncp.gov.br`)抓取全国招标 / 合同 / 年度采购计划,
清洗掉对中企无价值的标,补全背景(金额折算、地区、机构、品类),按六大行业分类,
导出中文 Excel;并在此之上做「中葡企业 ↔ 巴西竞标」匹配。

服务方向:数字经济 / 医疗医药 / 高端制造 / 大宗商贸 / 跨境电商 / 文化体育。

项目两部分:
- **爬虫**(`src/`):PNCP → 本地库 `data/procurement.db` → 中文报表 `data/exports/report.xlsx`
- **分析 / 匹配**(`analysis/`):企业↔竞标匹配 + 市场分析报告 —— 见 [analysis/README.md](analysis/README.md)

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
python -m src.cli fetch-contratos --start 2026-05-22 --end 2026-05-28 --page-size 500   # 已签合同(竞品)
python -m src.cli fetch-pca       --start 2026-05-22 --end 2026-05-28 --page-size 500 --limit 3000  # 年度采购计划
python -m src.cli fetch-catalogo  --tipo both          # 品类目录(一次性参考数据)
python -m src.cli enrich                               # 富化:金额折算 / 地区 / 机构画像 / 品类
python -m src.cli translate                            # 葡→中翻译(DeepSeek,按原文缓存)
python -m src.cli classify                             # 六大维度分类
python -m src.cli export-excel --out data/exports/report.xlsx   # 导出报表
```

一键报表流水线(自动跑上面合同→PCA→目录→富化→分类→导出):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_report.ps1
```

竞标匹配与市场报告的用法见 [analysis/README.md](analysis/README.md)。

## 数据

```
data/
├── raw/            原始抓取(留底)
├── procurement.db  本地库(SQLite,所有结构化数据)
├── inputs/         匹配用企业源表(输入)
├── exports/        报表与匹配交付表(输出)
└── filtered_log/   被过滤记录(审计,可追溯为何被丢)
```
