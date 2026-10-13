# 召回率复测（2026-10-12）

使用相同的68个冻结标注问题、30篇默认资料、30个片段，语料哈希与首次实验一致。

## 纯向量检索

运行 `.venv/bin/python -m evaluation.run_vector_retrieval --output docs/evidence/vector-retrieval-results-v2.json`。
全部分组指标与首次结果一致：Top1召回13.97%，Top3召回25.00%，Top5召回35.29%，Top10召回61.03%。采用与首次相同的查询Top10再截取前K方法，不代表逐个K单独请求或在线API测试。

## 当前聊天检索流程

当前 api/main.py 和 hosting/pipeline.py 已接入 search_with_rewrite。实验使用其真实实现，3秒阶段超时、9.5秒整体超时，Top3，关闭其内部证据检查，与聊天实际调用参数一致。在临时Chroma中预热后，以并发2测试全部68题。复用本地启动脚本的OpenRouter配置映射，没有替换当前指定模型。

模型 nex-agi/nex-n2.5-mini:free 的查询改写68次、重排68次全部返回HTTP404。每题只有原问题执行检索，取前5个候选，再因模型失败回退到原顺序的前3个。候选池召回率35.29%，最终Top3召回率25.00%，至少命中一个27.94%，全部指定依据找齐22.06%；双资料问题找齐0/8。

因此新流程接线已改变，但本次改写和重排没有成功执行，不能声称优化生效。需先修复模型可用性，再测成功改写/重排的效果。本次没有改变模型配置。

脚本：evaluation/run_current_retrieval.py。逐题原始结果：docs/evidence/current-chat-retrieval-results-v2.json。

首次当前流程尝试误用了继承的ANTHROPIC_BASE_URL与OpenRouter凭据组合，已在current-chat-retrieval-results-v1.json明确标记无效，不参与结论。v2已修正映射为OpenRouter原生调用。

边界：作者自编问题和标签、单次实验、内置小语料、真实本地Chroma与真实模型请求；不是在线HTTP全链路、用户导入语料、一万文件规模或最终回答正确率测试。
