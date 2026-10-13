# DeepSeek-V4.1-Flash 接入与 Top-K 实验（2026-10-13）

## 接入证据

按用户要求通过 OpenCLI 阅读 https://ark.volcengine.com/region:cn-beijing/docs/82379/1494384 。网站重定向到 /region:cn-beijing/docs/ark/chat-api?lang=zh，正文标题“对话(Chat) API”，接口为 POST https://ark.cn-beijing.volces.com/api/v3/chat/completions，使用 Bearer API Key。通过 OpenCLI document.body.innerText 核对正文起点、请求/响应示例及末尾更新时间；已存 ark-chat-api-opencli.txt。首次Markdown抽取包含大量图片且被截断，未将其声称为完整阅读。

使用当前 .env 中已有 ARK_API_KEY，不创建或输出密钥。方舟 GET /api/v3/models 确认 DeepSeek-V4.1-Flash 的ID为 deepseek-v4-1-flash-260910（ark-deepseek-models.json）。实际模型预检返回有效查询改写JSON，耗时3.014秒，响应52 tokens，见 ark-deepseek-preflight-v1.json。

沿用项目现有方舟适配器：OpenAI兼容Chat Completions请求，thinking.type=disabled。更新 .env ARK_MODEL，保留现有 LLM_PROVIDER=seed 方舟配置分支；business_model_config 实测选中上述DeepSeek模型及方舟URL。该分支历史命名为seed，但请求模型字段实际为DeepSeek。未改动Embedding、业务工具或模型客户端。已有适配器chat/stream单测通过，真实非流式请求预检通过；本次没有启动在线服务或执行最终回答/证据门控。

## 实验条件

复现：

```sh
.venv/bin/python -m evaluation.run_current_retrieval --provider ark --model deepseek-v4-1-flash-260910 --top-k 10 --concurrency 2 --output docs/evidence/retrieval-deepseek-topk-pool-new.json
.venv/bin/python -m evaluation.summarize_retrieval_topk --input docs/evidence/retrieval-deepseek-topk-pool-new.json --output docs/evidence/retrieval-deepseek-topk-summary-new.json
```

同样68题（30直接问法、30口语改写、8双资料问题）、30内置资料/30片段、冻结标签和相同语料哈希。临时隔离Chroma真实检索，保留原有3秒阶段超时、9.5秒整体超时，并发2。

每题保留原问题及最多3个改写问题，每个查询召回Top10；合并去重后使用项目原有LLM重排。三组共用这次检索和排序，仅改变最终截断数量为3、5、10。因此这是最终保留片段数对照，**不是分别把运行时top_k参数设为3/5/10的三次独立调用**；生产top_k=3/5的候选召回深度仍为5，不能将本次Top3直接称为生产聊天召回率。

标签只进入评分计算，不传给改写或重排模型。

| 最终保留数量 | 平均关键片段召回率 | 至少命中一个 | 全部指定依据找齐 | 双资料题找齐 |
| --- | ---: | ---: | ---: | ---: |
| Top3 | 66.18% | 70.59% (48/68) | 61.76% (42/68) | 1/8 |
| Top5 | 66.18% | 70.59% (48/68) | 61.76% (42/68) | 1/8 |
| Top10 | 70.59% | 75.00% (51/68) | 66.18% (45/68) | 1/8 |

召回率是逐题“找到指定片段数/应找到指定片段数”的平均，不是最终回答准确率。Top3与Top5相同，说明在本次排序中，第4、5位没有新增指定依据；不能据此断定更多片段对所有场景无用。

## 调用与失败

64/68次查询改写返回有效JSON，4次超过3秒；59次重排返回有效JSON，4次超时；另外5题候选不多于10条，按项目实现跳过重排。无HTTP404、429或其他HTTP失败。所有68题都有10条最终片段且无重复ID。超时降级结果保留在统计中，不丢弃失败题。

总耗时169.32秒。成功返回请求记录的usage总计114444 tokens（输入109206、输出5238）；超时请求没有usage返回，所以该数不是完整账单或费用估计。

## 结论边界

在固定候选池下，Top10比Top3平均关键片段召回率高4.41个百分点，找齐率高4.41个百分点；跨资料依据覆盖仍差（1/8找齐）。与之前纯向量Top3的25%或Nemotron阶段降级Top3的33.82%相比，模型、候选深度和请求成功率均有不同，不能将差额全部归因于模型更换。

这是作者标注的合成小规模问题和内置语料上的单次实验，没有独立人工标签复核、真实用户题、一万文件测试、在线服务或最终回答正确率验证。

原始结果：retrieval-deepseek-topk-pool-v1.json；指标汇总：retrieval-deepseek-topk-summary-v1.json。
