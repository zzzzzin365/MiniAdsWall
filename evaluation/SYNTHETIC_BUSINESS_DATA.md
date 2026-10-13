# 模拟广告业务数据 v1

`rag_evidence_business_cases_v1.json` 是本次生成的冻结评测数据：10 个业务快照、50 条广告、80 个问题。所有记录均为模拟，没有真实用户或真实账务数据。

每条广告包含现有演示字段 `id/title/price/clicks/video_count/videos`。额外的曝光、有效计费点击、固定CPC单价、订单、消耗、归因收入放在 `simulated_measurements` 中；它们仅服务评测，不表示项目已有真实数据接入。CPC结算规则、归因窗口和其他业务口径是显式模拟假设，不是项目实际合同。

每个业务快照有 8 个问题：

| 问题类型 | 数量 | 标准处理 |
| --- | ---: | --- |
| CTR 数据齐全 | 10 | 按同广告、同窗口算 CTR |
| 加价后的演示排序分 | 10 | 按现有公式模拟，不能称为收益或真实排名 |
| 固定 CPC 结算数据齐全 | 10 | 有效计费点击 × 固定单价，不能用页面点击 × 出价 |
| 历史 ROAS 数据齐全 | 10 | 同口径归因收入 ÷ 广告消耗，不能称为净利润 |
| 缺曝光次数 | 10 | 说明无法算 CTR，指出缺同窗口曝光 |
| 预测加价后的利润 | 10 | 历史快照不足以确定因果效果，需实验或有效预测依据 |
| 点击和曝光窗口冲突 | 10 | 不混算，索取同窗口数据 |
| 结算单价记录冲突 | 10 | 不猜一个价格，核实当前合同或账务流水 |

校准集 S01–S03 共 24 题；测试集 S04–S10 共 56 题。按快照切分，两组不共用广告记录。题目模板仍共享，所以 80 题不等于 80 种独立业务场景，也不能证明真实流量泛化能力。

每题均有 `answerable/reference/expected_values/expected_missing_information/gold_chunk_ids`。这些字段只用于评分，不能送给判断模型或回答模型。现有 runner 只给模型传 `question/chunks/business_data`；只给评审传参考答案和标签。排序分的参考值按现有广告工具验证，允许浮点舍入末位 0.01 差异。

## 使用

```bash
# 只验证测试集；这是原生成方式 vs 当前 LLM 证据检查，不是 Jev 对比。
.venv/bin/python -m evaluation.run_rag_evidence_ab \
  --dataset evaluation/rag_evidence_business_cases_v1.json \
  --split test --gate-version runtime-v2 \
  --output docs/evidence/rag-business-llm-new-run.json
```

真实模型执行会产生提供方调用与费用，需使用有效模型配置。现有脚本尚无 Jev 适配；已加入 Cloudflare Clef 的独立实验入口，比较“LLM筛片段 vs Clef筛片段”，保留相同检索输入、充分性检查、生成模型与提示。配置与命令见 [CLEF_EXPERIMENT.md](CLEF_EXPERIMENT.md)。

目前只完成数据校验与本地广告工具计算对照，没有调用 Jev、没有新模型实验结果。验证记录见 `docs/evidence/rag-business-synthetic-validation.json`。旧 20 题及其历史报告未覆盖。

如需新增数据版本，修改构建器并指定一个新的 `--output`；构建器拒绝覆盖已有文件。阈值只在 calibration 上调整，再固定阈值测试 test 集。
