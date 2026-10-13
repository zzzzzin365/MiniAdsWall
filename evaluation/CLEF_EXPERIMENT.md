# LLM vs Cloudflare Clef：只比较片段判断

已接通离线实验适配，未改变 `/chat`、`/search` 或托管任务的生产路径。真实结果需配置凭据后运行，模拟接口测试不算模型效果。

两组都使用当前 `runtime-v2` 证据流程：

| 报告字段 | 片段判断 | 充分性检查、缺失信息解释、最终生成 |
| --- | --- | --- |
| baseline | 当前 LLM | 当前 LLM |
| gated | Cloudflare Clef | 同一个 LLM、同样提示 |

每组检索片段、600 字截断、业务数据、8 秒检查期限相同。唯一替换是第一步片段判断。Clef 用一批 `noul` 问题返回每个片段能帮助回答的概率，转成现有 `support` 字段；LLM 的自评分与该概率并不等价，不能认为相同阈值就是校准。最终充分性仍由 LLM 判断，这个实验不能证明 Clef 可替代整个证据流程，也不能代表 Jev 的效果。

## 配置 Cloudflare

在本地 `.env` 或进程环境配置，不把密钥提交到仓库：

```dotenv
CLOUDFLARE_ACCOUNT_ID=你的32位账号ID
CLOUDFLARE_AUTH_TOKEN=具有Workers AI调用权限的Token
```

也支持 `CLOUDFLARE_API_TOKEN` 作为 token 变量别名。回答和评审继续用已有 `OPENROUTER_*` 或 `ANTHROPIC_*` 配置，须选可调用的模型。Cloudflare 模型不使用 OpenRouter 密钥。

## 运行

先用校准集做 2 题冒烟检查：

```bash
.venv/bin/python -m evaluation.run_rag_evidence_ab \
  --comparison llm-vs-clef --clef-model clef \
  --dataset evaluation/rag_evidence_business_cases_v1.json \
  --split calibration --limit 2 --interval 0 \
  --output docs/evidence/rag-clef-calibration-smoke.json
```

再跑完整 24 题校准集，只在该集调整阈值。冻结两个阈值后跑 56 题测试集：

```bash
.venv/bin/python -m evaluation.run_rag_evidence_ab \
  --comparison llm-vs-clef --clef-model clef \
  --dataset evaluation/rag_evidence_business_cases_v1.json \
  --split test --threshold 0.7 --clef-threshold 0.7 --interval 0 \
  --output docs/evidence/rag-clef-test.json
```

上面 0.7 是未校准的示例，不是推荐最佳值。选付费 OpenRouter 生成模型需追加已有 runner 的 `--allow-paid`。`--clef-model clef-flash` 可另跑一份报告；不能把两种模型的结果合并。脚本拒绝覆盖已有报告。报告 `threshold_calibrated` 始终保守记录 false，只有额外保存校准选择依据，才能说明阈值经过校准。

比较错误回答率、可回答问题的误拒答率、正确率、失败次数及完整链路耗时。HTTP 失败、缺失答案、非法概率和超时均记失败，不能算成功拒答。无片段时两组都只检查业务数据，不调用 Clef。8 秒期限包括实验 pacing，所以这里设置 interval=0；429 原样记录，当前适配不自动重试。

报告保存两组全部调用耗时和使用量，评审调用单独计费。Clef 输入费按 2026-10-11 官方价格估算：clef $0.24/百万输入 tokens，clef-flash $0.038，输出不计费；使用量缺失时费用未知。估算和提供方实际账单严格分开，本地计算成本不记零。报告不写密钥、请求头或 provider 异常正文。

评审仍是同一个 LLM，需人工检查错误与误拒答案例。数据仅有 8 类共享模板，不能从 56 题推断真实流量准确率。此实验只覆盖文本片段，不测试 Clef 的图片能力。

官方接口和响应字段：[Clef](https://developers.cloudflare.com/workers-ai/models/clef/)、[Clef-flash](https://developers.cloudflare.com/workers-ai/models/clef-flash/)、[输出 schema](https://developers.cloudflare.com/workers-ai/models/clef/schema-output.json)。

可选已有自托管服务：`CLEF_BASE_URL=http://127.0.0.1:8020`，脚本追加 `/v1/systemone`，可用 `CLEF_API_KEY`；远程地址须 HTTPS。不自动下载模型或启动服务。
