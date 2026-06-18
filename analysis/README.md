# analysis/ — 数据分析与匹配

独立于 `src/`(爬虫主代码)。所有脚本**从仓库根 `C:\巴西爬虫` 运行**(路径按 `match_lib.ROOT`/cwd 解析)。

数据分层:
- `data/inputs/` — **原始输入**(人工维护的企业源表,3 张 xlsx)
- `data/exports/` — **输出/交付物**(爬虫 report.xlsx + 两份匹配交付表)
- `data/raw/`、`data/procurement.db` — 爬虫原始抓取与主库(本目录只读)

两条交付线:

| 线 | 目录 | 交付物 |
|---|---|---|
| 竞标匹配 | `matching/` | `data/exports/中葡企业_..._需求匹配版.xlsx` 与 `..._全量匹配版.xlsx` |
| 市场报告 | `report/` | `report/outputs/巴西政府采购市场分析报告_v3.docx`/`.pdf` |

---

## matching/ — 中葡企业 ↔ 巴西竞标(两条链)

**两份交付物,差别 = 是否考虑时效:**
- **needfit(需求匹配版)**:只评需求契合,忽略时效/本地化/产能。
- **timed(全量匹配版)**:三轴评分 `0.45契合+0.30可行+0.25时效`,含时效门槛。

```
matching/
├── pipeline/
│   ├── run.py              统一入口:run.py {needfit|timed|all} [pre|post]
│   ├── match_lib.py        共享库:DeepSeek 客户端+缓存、葡语分类、路径常量、密钥(读 .env)
│   ├── shared/             两条链共用前置
│   │   ├── step1_company_master.py    企业主表(读 data/inputs 三张企业源表)
│   │   ├── step3_profiles.py          企业画像(DeepSeek)
│   │   └── step3b_profiles_fill.py    画像补缺
│   ├── needfit/            纯需求契合链(无时效)
│   │   ├── step10_needfit_pool_retrieve.py / step11_needfit_score.py / step12_needfit_categorize.py
│   │   ├── nf_fetch_pncp_data.py      PNCP 链接/ME-EPP 富集
│   │   └── step13_needfit_table.py    → _需求匹配版.xlsx
│   ├── timed/             含时效链(v3)
│   │   ├── step2_tenders.py / step4_retrieve.py / step5_score.py / step8_categorize.py
│   │   ├── v3_fetch_pncp_data.py      PNCP 富集(同步版)
│   │   └── step9_main_table_v3.py     → _全量匹配版.xlsx
│   └── legacy_enrich/     gen-1 富集工具(产无后缀缓存,被两链复用,少跑)
├── outputs/               产物:在用 json + 链接/ME-EPP 缓存
│   ├── cat_verify_in_nf/ + cat_verdicts_nf/   needfit 校验输入/裁决(裁决勿删)
│   └── cat_verify_in/    + cat_verdicts/       timed 校验输入/裁决(裁决勿删)
└── cache/                DeepSeek 应答缓存(可重生成)
```

运行(每条链有人工裁决断点):
```
python analysis/matching/pipeline/run.py needfit pre    # → cat_verify_in_nf/
#   外部/人工裁决写入 outputs/cat_verdicts_nf/
python analysis/matching/pipeline/run.py needfit post   # → _需求匹配版.xlsx

python analysis/matching/pipeline/run.py timed pre      # → cat_verify_in/
#   裁决写入 outputs/cat_verdicts/
python analysis/matching/pipeline/run.py timed post     # → _全量匹配版.xlsx
```

> 待办(代码去重):两条链的 PNCP 富集脚本与出表脚本里 `parse/meepp_label/classify/fix_scheme` 及链接/样式逻辑重复,计划抽进 `match_lib`(纯函数提取,不改输出)。

## report/ — 市场分析报告

```
report/
├── steps/   01_profile → 02_clean → 03_stats → 06_match_validate(产 stats2)
│            → 04_charts → 07_highvalue → 05_build_report → add_derivation_chapter
└── outputs/ 中间产物 + 报告 docx/pdf
```
按编号顺序从仓库根运行,例:`python analysis/report/steps/01_profile.py`。

## archive/ — 历史归档(只移动不删除)

被取代的死分支(保留可回溯):`matching_old_linear`(残留 v2 无后缀 step9_main_table)、`matching_v2_allocate`、`matching_top10_v4`、`matching_pf`、`matching_step12_bak`、`match_gen0`、`probes`、`report_old`、`exports_old`、`matching_outputs_dead`。

> `analysis/` 与 `data/` 均未纳入 git。重构前快照:仓库根 `_refactor_backup_20260616/`,确认两条链复跑正常后可删。
