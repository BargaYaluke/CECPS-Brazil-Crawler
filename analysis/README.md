# analysis/ — 数据分析(lite)

独立于 `src/`(爬虫主代码)。所有脚本**从仓库根 `C:\巴西爬虫` 运行**。

> **lite 重构(2026-06-18)**:原 `matching/`(企业匹配)、`ka_buyers/`(KA金主+多智能体验证)、
> `report/`(市场分析 Word)、`archive/`(死分支)四条线已删除。现只保留一条交付线:**设备产品采购表**。

数据分层:
- `data/inputs/` — 人工维护的企业源表(保留,本线不用)
- `data/exports/` — 输出/交付物
- `data/procurement.db` — 爬虫主库(本目录只读)

## equipment_bids/ — 设备/产品采购标(唯一交付线)

筛 20万–200万 CNY、未过期、面向设备/产品采购的标,DeepSeek 剔服务/工程。
**只用标的本身信息,不做企业匹配。** 详见 [equipment_bids/README.md](equipment_bids/README.md)。

```
equipment_bids/
├── find_bids.py          主脚本:筛选 + DeepSeek 分类 + 出 Excel
├── deepseek_client.py    自包含 DeepSeek 客户端 + 磁盘缓存(原 matching/match_lib 抽出)
├── cache/                DeepSeek 应答缓存(可重生成,gitignore)
└── README.md
```

运行:
```
python analysis/equipment_bids/find_bids.py        # 仅未过期
python analysis/equipment_bids/find_bids.py all    # 不限时效(含已过期)
```
产物:`data/exports/巴西设备产品采购标_20-200万CNY.xlsx`(+ `_全量含过期.xlsx`)。

> `analysis/` 的缓存/JSON 产物经 `.gitignore` 排除,仓库只跟踪 `.py` 与 `.md`。
