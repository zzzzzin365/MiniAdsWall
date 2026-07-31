# 固定意图识别测评集

`intent_test_set.json` 是意图识别的冻结测评集，只用于最终评测和回归对比，不参与 Few-shot、关键词、阈值或其他参数的调整。

## 数据规模

- 12 类业务意图：`query`、`complaint`、`request`、`greeting`、`escalation`、`technical`、`ads`、`ad_optimization`、`creative_generation`、`bid_strategy`、`account`、`feedback`
- 1 类兜底意图：`other`
- 每类 20 条，共 260 条
- 表达类型覆盖：正常表达、口语改写、错别字、模糊表达、超出能力范围

## 标注字段

- `expected_intent`：经过复核后的最终标签
- `annotation_status=confirmed`：两次标注结果一致
- `annotation_status=adjudicated`：边界或模糊样本经过争议复核后确定
- `variant`：样本表达类型，不作为模型输入

## 防止数据泄漏

固定集独立存放，不会写入 `core/intent_recognizer.py` 的 `_TEMPLATES` 或 `_PATTERNS`。加载时会自动检查：

1. 样本总数必须为 260；
2. 13 个类别必须各有 20 条；
3. ID 和归一化文本不得重复；
4. 必须覆盖五种表达类型；
5. 测评文本不得与 Few-shot 示例或单个关键词模板重复。

`frozen=true` 表示该版本不再参与调参。若业务标签发生变化，应新建数据集版本，不要静默修改历史基线。

## 统计口径

`IntentEvaluator` 统一输出：

- `accuracy`
- `macro_f1`
- `per_class`：每类 Precision、Recall、F1 和样本数
- `confusion_matrix`：真实标签到预测标签的完整矩阵
- `confusions`：只保留非对角线错误，便于快速定位主要混淆类别
