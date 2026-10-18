# 外部案例：视觉观察复用实验报告

日期：2026-10-15。仅外部案例图片；未读取商家素材、未改生产索引。

状态：partial，已保存121/240题次，未执行题不计正确或错误；实际完成数见表。
部分运行的主比较只用四组均已尝试的相同题（失败照计），不按成功与否筛题；尾部未配齐题次单列保留：{"all_observed_rows": 97, "matched_cases": 24, "unmatched_rows": 1, "all_observed_failures": 3, "comparison_rule": "compare only cases whose four arms were attempted, including failed arms; all rows retained in scores/trace"}

**以下是自动字段＋必要来源诊断，尚未完成人工盲审，不能直接称每句话都有依据或正式质量验收通过。**

> 本批次Seed mini也返回429 / SetLimitExceeded，模型设置用量上限触发。当前只完成121/240题次，主表用24道四组均已尝试的正式题；尾部1题次保留但不混入配对比较，重复诊断未运行。未重发被拒请求。

## 设置

计划6张归档外部图片、5个来源家族；Semrush单图6题开发，其余5张图、4家族30题测试。每图6个不同问题：2基础、2细节、1明确存在/不存在、1原图不能证明。计划主测试25题可回答、5题缺事实，实际覆盖见主表与逐题日志。金标准为本助手看原图后、运行前固定，非独立人工标注。

A=归档描述；B=每题读原图；C0=只读冻结通用观察；C1=根据已有观察判缺项，必要时补读并追加观察。路由模型、视觉模型及回答模型：{"vision": "doubao-seed-2-0-mini-260428", "answer": "doubao-seed-2-0-mini-260428", "router": "doubao-seed-2-0-mini-260428"}，全部在线调用计时计费。
通用观察不接收问题或gold；模型请求只接收去gold的请求。无整句问题视觉缓存、无答案缓存、无查询向量缓存。C0/C1共用同一初始观察，但状态各自隔离。

## 主测试（同图不同新问题）

| 组 | 可回答字段＋来源通过 | 缺事实题正确克制 | Seed在线读图 | 完整P50秒 | 完整P95秒 |
|---|---:|---:|---:|---:|---:|
| A | 40.00% | 100.00% | 0 | 1.23 | 1.90 |
| B | 90.00% | 100.00% | 24 | 6.92 | 10.29 |
| C0 | 75.00% | 100.00% | 0 | 1.64 | 3.09 |
| C1 | 75.00% | 100.00% | 2 | 3.51 | 6.39 |

C1相对B：诊断通过率差值 -15.00 个百分点；P95变化 -37.88%；在线读图减少 91.67%。
这不是相对A的双提升。C0−A还包括新增初始理解的信息量，不能独归因于结构化格式。

探索性配对区间：{"families": 3, "C1_minus_B_quality_ci95": [-0.4, 0.0], "C1_over_B_p95_ci95": [0.6149783540372838, 0.6634817820647598], "limits": "3 small families; exploratory intervals"}

## 成本与重复诊断

初始观察的每图Seed收据单列；cumulative-cost.json按每图N问把初始费用完整加回C0/C1，没有把它除给两组。若从旧批次导入相同Seed初始观察，初始usage是继承成本，新批次model_usage仅统计新提交，二者不重复收费。金额未查账单，money=null；tokens包括提供方报告的图片输入。重复诊断计划对汽车组件图与课程图各再运行两次、反向问题顺序，实际覆盖以repeated统计为准；每次重置C1状态，不复用最终答案或问题读图结果。

重复诊断：{}

模型请求与usage：{"doubao-seed-2-0-mini-260428": {"requests": 186, "prompt_tokens": 266746, "completion_tokens": 10016, "failed": 1}}

## 逐题差异与人工复核入口

| 题号 | A | B | C0 | C1 | C1路由 |
|---|---|---|---|---|---|
| zhihu-components-1 | 通过 | 通过 | 通过 | 通过 | reuse |
| zhihu-components-2 | 通过 | 通过 | 未通过 | 未通过 | read |
| zhihu-components-3 | 未通过 | 通过 | 未通过 | 未通过 | not_provable |
| zhihu-components-4 | 未通过 | 通过 | 通过 | 通过 | reuse |
| zhihu-components-5 | 通过 | 通过 | 通过 | 通过 | reuse |
| zhihu-components-6 | 通过 | 通过 | 通过 | 通过 | not_provable |
| xhs-duo-1-1 | 未通过 | 通过 | 通过 | 通过 | reuse |
| xhs-duo-1-2 | 未通过 | 通过 | 通过 | 通过 | reuse |
| xhs-duo-1-3 | 未通过 | 通过 | 通过 | 通过 | reuse |
| xhs-duo-1-4 | 未通过 | 通过 | 通过 | 通过 | reuse |
| xhs-duo-1-5 | 通过 | 通过 | 通过 | 通过 | not_provable |
| xhs-duo-1-6 | 通过 | 通过 | 通过 | 通过 | not_provable |
| xhs-duo-2-1 | 通过 | 通过 | 通过 | 通过 | read |
| xhs-duo-2-2 | 通过 | 通过 | 通过 | 通过 | reuse |
| xhs-duo-2-3 | 未通过 | 通过 | 通过 | 通过 | reuse |
| xhs-duo-2-4 | 未通过 | 通过 | 通过 | 通过 | reuse |
| xhs-duo-2-5 | 未通过 | 通过 | 未通过 | 通过 | reuse |
| xhs-duo-2-6 | 通过 | 通过 | 通过 | 通过 | not_provable |
| x-marketing-kb-1 | 通过 | 通过 | 未通过 | 未通过 | not_provable |
| x-marketing-kb-2 | 通过 | 未通过 | 通过 | 未通过 | reuse |
| x-marketing-kb-3 | 未通过 | 通过 | 通过 | 通过 | reuse |
| x-marketing-kb-4 | 未通过 | 通过 | 未通过 | 未通过 | reuse |
| x-marketing-kb-5 | 未通过 | 未通过 | 通过 | 通过 | reuse |
| x-marketing-kb-6 | 通过 | 通过 | 通过 | 通过 | not_provable |
| x-creative-framework-1 | 未执行 | 未执行 | 未执行 | 未执行 |  |
| x-creative-framework-2 | 未执行 | 未执行 | 未执行 | 未执行 |  |
| x-creative-framework-3 | 未执行 | 未执行 | 未执行 | 未执行 |  |
| x-creative-framework-4 | 未执行 | 未执行 | 未执行 | 未执行 |  |
| x-creative-framework-5 | 未执行 | 未执行 | 未执行 | 未执行 |  |
| x-creative-framework-6 | 未执行 | 未执行 | 未执行 | 未执行 |  |

## 证据边界

测试只有4个来源家族，25题里1题占4个百分点；不能可靠证明5个百分点非劣。每题每组1次主运行，P95受API抖动影响；重复episode不增加独立图片数量。所有错误、超时留在分母。
字段别名匹配不能自动识别否定、无关附加事实或不完整关系。scores.json保留每题原图支撑区域、答案和expected供人工复核，人工审查完成前不宣布正式质量达标。
本轮固定正确图片，不证明向量召回提升；没有视频、音频、视觉embedding或私有素材。观察更新无独立no-update组，不能把组合收益单独归功于更新。

复查：manifest.json、requests.json（无gold）、cases-gold.json、initial-observations.json、trace.jsonl、scores.json、receipts/、summary.json、cumulative-cost.json。

## 本次判断

自动字段诊断中，C1对B的可回答题通过率从90%降到75%（20题，少通过3题），超过预设5个百分点损失边界；P95约从10.29秒降到6.39秒，不能用提速掩盖质量损失。只有3个已完成测试来源家族、未独立人工审查且运行未完，结论仅为当前配置未证明质量非劣。另有C0格式失败2次。

## 下一轮素材

用户补充56张手串公开推广/商品图、28来源页、27家族，本地hash已验证，见 evaluation/visual_observation_bracelets_v1/assets.json。暂定6开发家族13图、21测试家族43图，需人工近似图审阅及独立gold标注后冻结；不混入本次6图的已运行结果。该批额外4张创作者图暂不纳入。风格/推定受众不能直接当视觉事实gold，图中营销宣称不等于已核验商品或效果事实。
