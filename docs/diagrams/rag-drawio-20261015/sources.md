# MiniAdsWall：RAG 全流程与实验位置

核对日期：2026-10-15。图中实线代表当前代码已接入，橙色虚线代表隔离实验；代码存在不等于生产环境验收通过。

## 当前流程

- 公共助手：问题 → 查询改写（最多四路）→ Chroma 余弦向量召回 → 去重 → LLM 重排 → 证据检查 → Agent 回答与来源。投放分析还会在证据检查与生成中使用服务端业务数据；图中只展开 RAG 部分。
- 创意工作台：问题与商品描述 → 服务端校验品牌、环境、素材版本 → 检索外部描述 Top3 和授权品牌素材描述 Top5 → DeepSeek 生成草稿 → 校验引用并展示原图或视频时间点。这条路径没有自动继承公共助手的查询改写、LLM 重排与证据检查器。
- 公共索引：通用文本、外部文章和归档图片描述进入 `knowledge_base_cosine_v1`。
- 品牌素材索引：Seed 理解图片或最多八个视频抽样帧，结果写入 MySQL；描述分块后进入 `studio_media_caption_cosine_v1`。分块和索引也可能在首次检索时补建，不全是后台离线任务。
- 目前使用文本 embedding 检索描述，没有原生图像 embedding 或音频理解。
- 实验 E3/E4 位于检索后、最终回答前的视觉证据准备。E4 固定正确参考图，不能证明检索召回改善；按需读原图尚未接入工作台业务链路。

## 实验与结果

| 实验 | 对照与数据 | 结果及边界 |
| --- | --- | --- |
| E1 公开检索 | 43 个公开片段；纯向量 vs 标题/正文词法融合；另测查询向量缓存 | 新措辞可回答题语义 Recall@5 51.67% → 76.67%；50%受控缓存命中时检索 P95 458.30 → 274.32ms。全新查询未证明稳定提速；不含生成。 |
| E1 早期改写小试 | 13 个外部描述片段、24 道可回答题；原查询 vs 改写后 RRF 融合 | 来源 Recall@3 77.08% → 72.92%。与 E1 新措辞测试语料、问题和 Top-K 不同，不能直接合并比较。 |
| E2 合成搜索 | 10 万随机向量 HNSW 参数；3600 万随机向量 float32 vs 二值粗筛后浮点精排 | 高召回 HNSW 在该样本/机器上更慢；3600万的8查询整批 114.64 → 66.03秒，但邻居 Recall@5 只有62.5%。不能冒充真实语义召回或单请求P95。 |
| E3 早期原图补读 | 4张商家图、20题；每题读图 H vs 关键词按需读图 R | 可回答题字段＋必要来源通过率均93.75%；读图20→12次，P95 26.24→29.57秒。固定候选来源，小样本。历史商家数据实验单列，不纳入后续外部图实验。 |
| E4 观察复用 | 6张外部图、四组；Seed mini续跑完成121/240题次，主比较24题四组配对 | 每题读图 B vs 缺项补读 C1：通过率90%→75%，P95 10.29→6.39秒，在线读图24→2次。自动字段诊断、未完成人工盲审，用量上限中止，质量未达标。 |
| 待执行 | SQ8与扩大二值候选；56张外部手串图 | SQ8只有方案；56图完成文件校验及暂定分组，未跑模型实验。 |

各实验独立，无法把不同对照组的质量和速度提升拼成一次“全流程双提升”。E4 的“题次”指一次问题与实验组组合，不是121道独立问题；初始理解成本在原报告单列。

## 核对的源码与报告

- `api/main.py`：`_prepare_knowledge_context`、`/search`、聊天调用。
- `mcp/tool_manager.py`：查询改写、并行召回、去重、LLM重排。
- `mcp/knowledge_base.py`：Chroma、MiniLM、文本分块和余弦索引。
- `core/rag_evidence.py`：证据检查。
- `api/studio.py`：素材和外部案例检索、DeepSeek生成、引用校验、Seed理解。
- `core/multimodal_rag.py`：授权描述分块、余弦Top5、精确chunk ID范围。
- `mcp/external_marketing.py`：外部来源描述、公共索引与Top3。
- `apps/mini-ad-wall/server/services/merchant/studio/knowledge.ts`：服务端品牌/环境/版本范围。
- `docs/validation/general-rag-ab/20261014-v2/report.md`
- `docs/validation/multimodal-rag-ab/20261014-pilot-v1/report.md`
- `docs/validation/multimodal-rag-ab/20261014-images-v1/report.md`
- `docs/validation/visual-observation-ab/20261015-seedmini-v2/report.md`
- `docs/rag-quantization-ab-plan.md`
- `evaluation/visual_observation_bracelets_v1/assets.json`

本文件夹交付的是可编辑 draw.io XML；原图的位图编辑因网络错误未生成PNG。
