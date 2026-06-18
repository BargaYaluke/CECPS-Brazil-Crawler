# equipment_bids/ — 设备/产品采购标筛选

**只用标的本身信息,不做企业匹配。** 从主库 `data/procurement.db` 筛出「20万–200万 CNY、未过期、面向设备/产品采购」的标。

**交付物(两份)**:
- `data/exports/巴西设备产品采购标_20-200万CNY.xlsx` —— **active 模式**,仅未过期 69 标
- `data/exports/巴西设备产品采购标_20-200万CNY_全量含过期.xlsx` —— **all 模式**,不限时效 1544 标(含 `时效状态` 列,69 未过期 + 1475 已过期)

每份 3 个 sheet:
- sheet1 `设备产品采购标`:物资类别 / 时效状态 / 标的中文梗概 / 主要采购物品 / 金额(万CNY+BRL) / 采购机构·级别·州·城市 / 招标方式 / ME-EPP限制 / SRP / 长期挂网 / 发布·截止日·剩余天数 / 标的葡文原文 / 投标链接 / PNCP官方链接 / PNCP编号
- sheet2 `类别汇总`:各物资类别 标数/未过期标数/金额合计/单标中位
- sheet3 `剔除_疑似服务工程`:候选池里被判为服务/工程施工而剔除的,留作回溯

## 口径
1. **未过期** `data_encerramento_proposta > 2026-06-18`
2. **金额** `valor_cny_estimado ∈ [20万, 200万] CNY`(1 BRL≈1.34135 CNY;库内 `valor_cny_estimado` 全覆盖)
3. **实物采购** `itens` 表 Material(M)金额占比 ≥ 0.8 → 候选池(99);剔 `Leilão`(资产变卖非采购)
4. **去服务/工程** ⚠ 源数据部分服务项被误标 material='M'(理疗/住宿/仲裁/铺路/维修等),故再用
   DeepSeek 按 objeto+物品葡文描述判定 `is_prod` 并归 14 类,剔 `服务(非实物)`/`工程施工` →
   最终 69 产品标。中文梗概复用 `translation_cache`(免费,103/104 命中)。

## 跑(从仓库根 `C:\巴西爬虫`,python 完整路径见 [[python_env_brazil_crawler]])
```
python analysis/equipment_bids/find_bids.py        # active:仅未过期(默认)
python analysis/equipment_bids/find_bids.py all    # all:不限时效(含已过期)
```
- 复用 `analysis/matching/pipeline/match_lib.py` 的 DeepSeek 客户端+磁盘缓存(改提示词→缓存失效会重算)。
- 分类结果落 `classifications.json`(可回溯)。

## 类别分布(产品标 69 / 合计 4478 万CNY)
药品15 · 食品农产10 · 医疗器械耗材10 · 其他实物9 · 办公教学家具6 · 建材五金物料6 ·
车辆工程机械5 · IT电子通信3 · 厨房家电2 · 燃料化工品2 · 工业生产设备1。
> 「设备/制造业产品」更可投的核心子集 ≈ 车辆+工业设备+IT+厨房家电+建材+医疗器械(约 27 标);
> 药品/食品虽属「产品」但本地化/监管强,可按需过滤。注意多条 ME/EPP「全部专属」外企不可直投。
