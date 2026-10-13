# L2 → 余弦距离：控制变量实验（2026-10-13）

## 已落地修改

mcp/knowledge_base.py 使用新 collection knowledge_base_cosine_v1，显式设置 hnsw:space=cosine。已有 knowledge_base 索引的距离不可原地修改，因此批量复制已有ID、原文、metadata及向量，保留旧集合供回退；不重新计算导入资料的Embedding。迁移中断可按缺失ID继续，不覆盖新集合中的修改；独立knowledge_base_migration_state记录完成状态，避免重启后复活用户已删除的旧资料。迁移时应停止旧版本写入；当前数量检测不能识别所有同数量并发更新。

本地配置真实索引验证：本地模式，新旧各30片段，新集合metadata确认为cosine，完成标记已写入。该验证不是运行中的服务热切换。下次启动KnowledgeBase会选新集合。对应证据：cosine-runtime-index-validation.json。

实验入口 run_current_retrieval、run_vector_topk 已跟随实际cosine集合；run_vector_retrieval默认cosine，可用--distance l2显式复现旧基线。

## 控制变量方法

68道相同题、30篇相同资料，复用上轮DeepSeek-V4.1-Flash真实执行记录（retrieval-deepseek-topk-pool-v1.json）。冻结其查询改写、失败降级及最终片段ID排序；**本轮不重新调用DeepSeek**，避免模型随机生成和网络超时成为额外变量。

建立两个真实Chroma索引，一组l2、一组cosine，共享逐值相同的30个文档向量。260个原始/改写查询只向量化一次，两个索引复用同一查询向量；Embedding、文档、ID、HNSW默认参数、候选数量均相同。对每个查询检查完整30片段顺序，并复现每题合并候选池。候选一致时回放上一轮冻结的最终ID排序，按Top3/5/10截断。断言L2组候选集及最终Top10精确复现旧结果；查询/文档向量哈希写入原始报告。

复现：`.venv/bin/python -m evaluation.compare_vector_metrics`（已有结果拒绝覆盖）。

## 结果

| 最终保留数量 | L2平均召回率 | cosine平均召回率 | L2/cosine至少命中一个 | L2/cosine全部依据找齐 |
| --- | ---: | ---: | ---: | ---: |
| Top3 | 66.18% | 66.18% | 70.59% | 61.76% |
| Top5 | 66.18% | 66.18% | 70.59% | 61.76% |
| Top10 | 70.59% | 70.59% | 75.00% | 66.18% |

260个查询完整片段排序变化0个；68题合并候选池变化0个。跨资料题两组均1/8找齐。

## 为什么没有提升

当前Chroma默认ONNX all-MiniLM-L6-v2输出向量已经归一化。本次实测文档和问题向量长度范围都在0.9999999404到1.0之间。对单位向量u、v：

平方L2距离 = ||u-v||² = 2-2(u·v) = 2 × cosine距离。

两种距离因此给出相同的顺序。实际距离关系最大残差约9.25e-8，本次无排名变化；不外推到其他未归一化模型或更大近似索引。

## 用余弦后的不同之处

之前score=1-L2距离，不是余弦相似度。现在score=1-cosine距离，数值是余弦相似度（理论范围[-1,1]），更容易解释方向相近程度，但仍不是正确概率。示例“CTR怎么算？”的第一条错误匹配仍是“广告运营风险边界”，分数从0.1484变为0.5742；数值升高不表示相关性提高。

若后续用向量score设置硬阈值，需重新校准；本项目证据门控的support阈值是另一种模型判断分，不应机械替换。冻结排序实验没有测重新调用LLM时新score对重排、证据检查或回答内容的影响。

## 验证与边界

余弦索引迁移与RAG运行时回归共30项测试通过，包含新库配置、导入文档/metadata/向量保留、旧集合保留、重复启动、删除不复活、错误距离配置拒绝。

只验证内置小语料、作者标签、真实Chroma向量距离以及冻结DeepSeek输出的重放；不代表新一轮模型生成质量、在线服务、真实用户题、一万文件规模或最终回答准确率。仅改成余弦不会修复当前中文向量检索的漏检问题。

原始结果：l2-cosine-controlled-v1.json；实现：mcp/knowledge_base.py；脚本：evaluation/compare_vector_metrics.py。
