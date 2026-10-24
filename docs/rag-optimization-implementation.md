# RAG 优化：实现与运行

日期：2026-10-24。对应 [优化 SDD](rag-optimization-sdd.md)。当前状态：首版代码与本地功能验证完成，默认关闭；未部署，未通过 SDD 的正式质量、耗时和成本发布门槛。

## 已落地

| 能力 | 实现位置 | 行为 |
| --- | --- | --- |
| 原文与索引版本 | [index.py](../core/rag/index.py) | SQLite 原文、正文位置、版本、片段与发布指针；Chroma 使用独立 generation 集合 |
| 分块 | [index.py](../core/rag/index.py) | 识别 Markdown 标题边界；父段最多 1,600 字符；子段最多 400 字符、重叠最多 80 字符；长句继续切分 |
| 混合召回 | [service.py](../core/rag/service.py) | BM25 与向量召回；按排名融合；去重；固定请求内 generation；记录分支失败 |
| 统一入口 | [tool_manager.py](../mcp/tool_manager.py) | 同步检索、同步回答和托管助手共用新服务；旧链路保留 |
| 重排序 | [tool_manager.py](../mcp/tool_manager.py)、[reranker.py](../core/rag/reranker.py) | none / llm / dedicated；大模型输出必须是完整、唯一、合法的索引排列；失败回退融合排名 |
| 父段补全 | [service.py](../core/rag/service.py) | 同版本补读、合并父段、预算裁剪保留命中位置；实际文本范围产生证据 ID；送现有证据检查 |
| 引用校验 | [citations.py](../core/rag/citations.py) | 结构化回答、证据 ID、连续原文摘录、业务记录名称；标题与 URL 由服务端渲染 |
| 输出时复核 | [main.py](../api/main.py)、[pipeline.py](../hosting/pipeline.py) | 生成后重验正文版本与文档撤销；新版托管回答缓冲后再输出，失败原文不推给用户 |
| 观测 | [usage.py](../core/rag/usage.py) | 改写/重排调用收据、实际 token、已知费用；缺失费用保持 null；阶段耗时与降级原因 |
| 操作与评测 | [rag_index.py](../scripts/rag_index.py)、[run_rag_optimization.py](../evaluation/run_rag_optimization.py) | 构建、导出、回滚、撤销；冻结版本的向量/混合召回对照 |

新链路不复用旧工具结果缓存，确保每次读取当前版本与撤销状态。旧链路缓存不变。开启影子模式只比较新版单查询召回，不改变实际答案，也不增加模型调用。

## 首版实现选择与限制

- 仅支持服务端维护的公共文本知识池；输入其他 scope 会被拒绝。没有新增私有品牌知识授权入口，不能宣称完成多租户隔离。
- BM25 使用固定的中文双字片段与 ASCII 标识符分词，不依赖外部分词包；保留 CPA、CTR、AD-123 等完整标识符。未实现用户词典与别名表，需后续独立评测。
- 首版最多 1,000 篇文档、10,000 个片段；关键词检索在这些有界片段上计算，尚未建立持久倒排结构。不能以此宣称支持百万文档或并发性能提升。
- 为避免 tokenizer 与生成模型不一致，证据正文暂用 4,000 UTF-8 字节的保守预算；这是上界估算，非供应商实际 token 计量。完整请求仍受现有入口限制；尚未统一接入各生成模型 tokenizer。
- 文档首次未提供 doc_id 时按 knowledge_id 或“URL＋标题”生成；导出会保留 doc_id。后续改名请保留该 ID。修改正文/来源信息生成新 revision。
- 新版写入采用完整文档快照离线发布。开启后原在线新增/上传接口返回 409，避免写成功却不进入活动索引。旧导入文档若仅剩片段，不自动猜测原文边界；需要原始文本后再迁移。
- dedicated 适配器只加载明确配置的本地 safetensors 标量重排模型，默认 CPU，不下载模型、不执行远程代码；记录文件指纹。已测试选择与故障回退，未用真实专用模型完成质量和性能验收。
- 引用校验验证结构、出处与摘录，不证明整句事实正确。引用功能默认关闭，须与生成模型做完整回答质量验收后再启用。
- 改写、重排及现有证据检查有模型收据；当前托管生成流没有完整供应商费用收据，因此不能宣称已达到端到端成本验收。

## 构建与切换

以下在仓库根目录执行；使用与 API/Worker 一致的 Chroma 地址和 RAG_V2_STORE。构建脚本从进程环境读取配置，不自动读取 shell 未导出的变量。单机 API 与 Worker 必须共享 SQLite 文件；多机部署需要共享版本存储与发布机制，不在本轮交付范围。

```bash
# 导出内置完整文档；若有用户导入知识，先合并其原文并明确 doc_id。
.venv/bin/python scripts/rag_index.py export-defaults --output /tmp/rag-source-documents.json

# 独立集合构建成功、核对全部片段 ID 并通过查询检查后才发布。
CHROMA_PORT=8001 .venv/bin/python scripts/rag_index.py build --documents /tmp/rag-source-documents.json
.venv/bin/python scripts/rag_index.py status
```

每次 build 是一份完整快照，会替换新链路活动文档集合，不是增量追加。先导出当前版本，再编辑、补充、删除文档并构建；不要只提交新增一篇而遗漏其他文档。

```bash
.venv/bin/python scripts/rag_index.py export --output /tmp/rag-current-documents.json
```

索引就绪后，通过 API 与 Worker 启动环境逐步配置（[配置示例](../.env.example)）：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| RAG_V2_ENABLED | false | 使用新链路；没有已发布版本则启动失败 |
| RAG_V2_SHADOW_ENABLED | false | 旧链路继续回答，同时观测新链路单查询召回 |
| RAG_V2_STORE | Chroma 本地目录下 rag-text-v2.sqlite3 | 原文、版本指针和撤销表，须与构建命令一致 |
| RAG_V2_HYBRID_ENABLED | true | 混合检索；false 仅向量 |
| RAG_V2_REWRITE_ENABLED | true | 多查询；单独比较混合召回时设 false |
| RAG_V2_PARENT_ENABLED | false | 补全父段 |
| RAG_V2_RERANKER | llm | none、llm、dedicated |
| RAG_V2_RERANK_MODEL_PATH | 无 | dedicated 所需本地模型目录 |
| RAG_V2_CITATIONS_ENABLED | false | 结构化引用与输出校验 |

这些配置不代表服务已重启或已上线。首版灰度以明确的 API/Worker 实例配置分组进行，未自动实现按流量百分比分组。

```bash
# 回滚到已发布 generation；不会恢复已撤销文档。
.venv/bin/python scripts/rag_index.py rollback --generation <generation_id>

# 立即撤销指定文档；活动版本与历史版本均不可继续引用。
.venv/bin/python scripts/rag_index.py deny --doc-id <doc_id>
```

回滚代码路径时将 RAG_V2_ENABLED=false 并重启目标实例。撤销表只约束 v2，退回旧链路前必须从旧知识池同步移除相应文档；不得将代码回滚当成跨两套知识池的撤权同步。

## 验证与效果边界

本地测试使用临时 SQLite、真实 Chroma 和测试向量，生成模型使用替身；覆盖版本发布、错误片段集合拒绝发布、重叠与长句、源位置、混合召回、分支故障、重排错误、父段补全、取消、HTTP 回答与托管输出、生成期间撤销、引用与摘录校验。托管旧测试明确查询 message 历史，避免将工具记录当回复。

```bash
AGENT_SYNC_DURABLE_ENABLED=false AGENT_MEMORY_SUBJECTS='' AGENT_EXPERIENCE_READ_ENABLED=false \
.venv/bin/python -m unittest \
  tests.test_rag_optimization tests.test_cosine_knowledge tests.test_delivery_advice \
  tests.test_rag_evidence_runtime tests.test_rag_evidence_hosting \
  tests.test_rag_evidence_gate tests.test_hosted_performance_tools -q
```

这三个环境覆盖仅隔离开发机持久会话、记忆灰度名单和经验读取配置，不修改应用默认值。测试中未请求付费模型。

另用真实本地 MiniLM CPU 嵌入执行了 30 篇内置文档、6 道自建题的独立冒烟对照：[原始结果](validation/rag-optimization/local-embedding-smoke-v2.json)。只测原问题向量召回与向量＋BM25 融合的前 8 条，不包含改写、重排、父段补全、证据检查或答案生成，不代表当前完整链路 A 组。

```bash
.venv/bin/python evaluation/smoke_rag_optimization.py --output /tmp/rag-local-smoke.json
```

正式冻结题集的 gold 使用原文 doc_id、revision、start/end，而非旧分块 ID：

```json
{"cases":[{"id":"Q001","family":"billing-rules","split":"test","query":"规则是什么？","gold":[{"doc_id":"文档ID","revision":"正文版本","start":0,"end":20}]}]}
```

```bash
.venv/bin/python evaluation/run_rag_optimization.py --cases /path/to/reviewed-cases.json --output /tmp/rag-comparison.json
```

评测脚本固定 generation、题集 hash 和文档清单，拒绝来源版本不符、跨集合文档家族泄漏，以及覆盖旧结果文件。失败请求保留，必要证据覆盖按原文区间去重合并。当前只实现 C0/C1 的检索对照，尚未覆盖 SDD 全部分组、区间估计或最终回答人工评分。

## 尚未完成的发布条件

1. 至少 120 题、按文档家族拆分并经人工复核的正式题集。
2. A—G 全部分组、最终回答质量、误拒答、模型费用与同硬件并发耗时对照。
3. 专用重排模型与具体供应商生成模型的真实验收。
4. 对已有导入文档的原文迁移、影子流量记录与实例灰度发布。

因此本次仅将首版实现标为完成；SDD 正式验收与生产灰度保持待办。
