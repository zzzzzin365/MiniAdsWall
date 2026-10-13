# 换模型召回实验（2026-10-12）

模型从 nex-agi/nex-n2.5-mini:free 换为 nvidia/nemotron-3.5-lightning:free；项目 .env 的 OPENROUTER_MODEL 已更新，凭据未更改。真实预检返回有效查询改写，耗时1.647秒，记录于 retrieval-model-preflight-v1.json。

复现：`.venv/bin/python -m evaluation.run_current_retrieval --model nvidia/nemotron-3.5-lightning:free --output docs/evidence/current-chat-retrieval-lightning-new.json`

相同68题、30篇默认资料、30片段、相同冻结标签、相同语料哈希。复用当前search_with_rewrite；Top3、阶段3秒、整体9.5秒，并发2，真实模型和隔离Chroma。没有改Embedding、语料、超时、重排提示或金标，没有调用回答模型或证据门控。总实验耗时107.87秒。

| 指标 | 旧模型全部404后的降级结果 | 新模型实测（含降级） |
| --- | ---: | ---: |
| Top3关键片段平均召回率 | 25.00% | 33.82% |
| 至少命中一个关键片段 | 27.94% | 36.76% |
| 全部依据找齐 | 22.06% | 30.88% |
| 重排前候选池召回率 | 35.29% | 41.18% |

Top3召回率增加8.82个百分点。单资料问题指定片段命中从15/60增至21/60；双资料问题仍4/8只命中一条、0/8找齐。改善6题，没有题退步。

调用边界：68次改写中31次返回有效JSON，31次HTTP429，5次超时，1次HTTP502；68次重排中35次返回有效JSON，33次HTTP429。每题都有最终结果，但很多题走了降级。新模型实测结果不是“所有模型步骤成功时”的纯效果，不能推断稳定提升或真实流量准确率。429是HTTP限流响应，本实验未解析到可靠的限流窗口，不能声称是日配额耗尽。

没有通过放宽超时、换题、调标签或丢弃失败题抬高指标。要进一步评估稳定效果，需要遵守提供方限流窗口、降低请求频率再重复实验，并新增独立标注题。

逐题数据及模型调用状态：current-chat-retrieval-lightning-v1.json。原基线：current-chat-retrieval-results-v2.json。纯向量层没有换模型，其召回率仍是25%。
