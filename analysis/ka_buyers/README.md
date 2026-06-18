# ka_buyers/ — 巴西金主(KA)竞标分析表

独立于 `matching/`(那条是「固定 437/77 家在册中企 ↔ 竞标」匹配)。本线是**买家中心 + 全中国开放荐企**:
先筛金主(KA 买家),再对其每条标做精细分类,并从**全中国**范围匹配 5 家**强出海意愿**中企(不限于在册名录)。

**交付物**:`data/exports/巴西金主KA竞标分析表.xlsx`
- sheet1 `金主竞标主表`(1519 标):KA排名/采购机构/级别/州/城市 · 一级行业/二级专业领域/需求类型/长期挂网/SRP · 竞标中文梗概/金额万CNY/提交截止日期/剩余天数/ME-EPP限制 · **推荐中国企业TOP5(强出海·经核验)** · PNCP编号/投标链接/PNCP官方链接
- sheet2 `金主KA清单(200家)`:每家金主画像(发标数/总额/单标中位·最大/KA综合分)

## 口径(用户 2026-06-17 确认)
- **金主 = 综合分 top-200 采购机构**:`r_freq*0.45 + r_总额*0.35 + r_单标中位*0.20`(在剔>5亿数据坑的全量池上算)。结果 158市政/29州厅/9联邦/4国企,覆盖其**未过期标 1519 条**。
- **荐企 = DeepSeek 海选 + Claude 联网复核**。公司来自全中国、优先出海意愿强,不在现有 437 家表里。
- **行规模 = 全部未过期 KA 标(1519)**。

## 流水线(从仓库根 `C:\巴西爬虫` 跑;python 用完整路径见 [[python_env_brazil_crawler]])
```
step1_ka_pool.py      金主综合分→top200→未过期标;本地富集 链接/ME-EPP(itens表)/SRP/长期挂网/截止日。无需PNCP在线API。
step2_classify.py     DeepSeek 批量 12/调:一级行业(固定表)+二级专业领域+需求类型(7类)。→ classifications.json
step3_candidates.py   按(一级,二级)桶(rare<3→『综合』)DeepSeek 海选~12家强出海中企/桶。→ candidate_pools.json + bucket_index.json
step4a_uniq.py        852家唯一候选,按主一级行业分组。
step4b_prep.py        切 80 批(≤12/批)→ outputs/verify_in/g*.json
[Workflow]            80个 sonnet 智能体:Read批→知识+WebSearch核验真实性&出海强度→Write outputs/verify_out/g*.json
_repair_verdicts.py   修复个别 evidence 内未转义引号的坏 JSON(行级重转义)。
step4c_assemble.py    汇总裁决→每桶『经核验名册』(剔 real=false/出海=无;按 出海强度→巴西→原序)。→ rosters.json
step6_verify_amounts.py 金额核验:report预估金额 vs DB抓取值 vs itens逐项汇总 + FX一致性 → amount_audit.json(每标 verify_cat)。
step5_build_excel.py  每标按 需求类型↔能力 过滤+出海强度/巴西/体量契合 排序取5;**主表按金额降序、预算保密沉底**;含『金额核验』列;出 Excel(主表+金主清单)。
```

## 金额核验结论(2026-06-17)
report 预估金额 = DB 抓取值 **完全一致**(0 笔误);FX 汇率统一 **1 BRL≈1.34135 CNY**。每标 `金额核验` 列四类:
逐项预算一致✓ 1043 / 估算上限·逐项预算仅部分(SRP框架/多年合同上限,itens抓取不全,非笔误)407 / 预算保密(sigilo)68 / 象征性·未计价(credenciamento占位0.01BRL)1。**未发现金额虚高笔误**。主表已改为**金额从大到小**排序,68保密+1象征性沉底。

## 关键数据事实
- `itens` 表 `tipo_beneficio_nome` 本地齐备(99.6%覆盖)→ ME/EPP **无需 PNCP API**:全标专属174/部分155/预留43/分包1。
- 投标链接来自 DB `contratacoes.link_sistema_origem`(1038/1519);**PNCP官方门户链接**由 PNCP编号确定性构造 `app/editais/{cnpj}/{ano}/{int(seq)}`(100%)。
- 长期挂网 = is_long_term_opportunity 或 Credenciamento(56 条);SRP价格登记 662 条。

## 核验产物(勿轻删)
- `outputs/verify_out/*.json` = 80批 Claude 联网核验裁决(846家,706保留/146剔除),重生成要重花 token+联网。
- `outputs/verdicts_merged.json` / `rosters.json` 可由上面重新汇总。
- DeepSeek 应答走 `analysis/matching/cache/`(共享);改提示词→缓存失效会重新计费。
