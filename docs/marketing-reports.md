# 业务指标与报表

入口：广告管理页面下方的“数据分析”→“投放效果”，与“广告概览”的出价/排名图表在同一模块内切换，不再跳转独立页面。切换视图保留查询条件、报表和估算结果；即使旧广告列表为空，也可查询营销活动。旧链接 `/?marketing=1` 兼容为在广告管理页面直接选中“投放效果”。营销 API 仍默认关闭，需要完成迁移并配置原有品牌归属、来源和 `MARKETING_EVENTS_ENABLED=true`；没有自动修改真实主库或打开开关。

## 接口

- `GET /api/marketing/reports`：运营认证；必填 brand_id、campaign_id、environment、start、end、timezone、rule_version。start/end 是 UTC 时间，区间 `[start,end)`，最多 31 天；timezone 支持 Asia/Shanghai、UTC；规则固定 last-click-v1。
- 素材筛选同时传 creative_id、creative_version。环境必须与服务相同，活动/素材必须属于授权品牌。重复查询创建新不可变 report_id；不会覆盖旧记录。
- `GET /api/marketing/reports/:id`：按 ID 回查当时的快照，重新检查当前品牌权限和内容哈希。旧快照的 coverage 是当时状态，不代表今天仍完整。
- `POST /api/marketing/metrics/estimates`：运营认证，只计算用户假设，既不写入报表事实，也不调整排序、预算或扣费。

示例查询：

```text
/api/marketing/reports?brand_id=brand_a&campaign_id=CAMPAIGN_ID&environment=synthetic&start=2026-10-01T16%3A00%3A00.000Z&end=2026-10-02T16%3A00%3A00.000Z&timezone=Asia%2FShanghai&rule_version=last-click-v1
```

Shanghai 2026-10-02 整日对应上述 UTC 区间。API 也支持非整日时间窗口，按事件时间精确查询，不拿整个日分区冒充一小时结果。

## 指标口径

| 返回字段 | 定义 |
| --- | --- |
| ctr | 窗口有效点击 / 有效曝光 |
| cvr | 点击窗口内点击所归因的支付订单 / 窗口有效点击；不是同日订单/点击 |
| cpa_minor | 窗口费用 / 窗口支付时间的归因订单 |
| cpc_minor | 窗口费用 / 窗口有效点击 |
| cpm_minor | 窗口费用 / 窗口有效曝光 × 1000 |
| roas | 窗口支付时间的归因净收入 / 窗口费用 |
| refund_rate | 窗口支付订单中有正金额退款的订单 / 窗口支付订单 |
| realized_revenue_per_mille_minor | 窗口支付归因净收入 / 窗口曝光 × 1000；历史跨期运营指标，不是预测 eCPM |
| ecpm_minor / ltv_minor | 真实报表缺预测模型/稳定客户生命周期数据时返回 null 和缺失原因；估算接口单独计算 |

每项返回 value、unit、status、reasons。value 为六位小数的十进制字符串，避免金额乘法的浮点误差；ratio 表示比值（0.1 即 10%），minor 表示人民币分。零分母返回 null。CTR/CVR 超过 100% 不截断，并给出数据质量/一点击多订单提示。

费用必须由全部冻结来源的最新 complete 核对区间覆盖查询窗口；新流水/更正使旧覆盖失效，来源配置变化也视为不完整。无来源、核对缺失/失败时 CPA/CPC/CPM/ROAS 为 null。无费用记录不等于确认花费为零。允许大标记覆盖子区间、相邻完整标记拼接；存在重叠失效标记时保守判定不完整。

报表使用 REPEATABLE READ 一致快照组合有效事件、当前费用、已发布归因关联和来源覆盖。published_input_epoch 未跟上原始 input_epoch 时状态 stale，归因订单/净收入与所有比值返回 null，避免混用数据版本。无历史事件且无输入 epoch 时可报告零样本，但不能因此宣称费用完整。

received_cutoff 为归因 Worker 发布时的接收截止点，generated_at 为生成报告时间，evidence 包括 input_epoch、published_input_epoch、generation、相关日分区 revision、待处理队列。查询结束 +7 天为归因观察截止，再 +30 天为迟到回传观察截止；未结束标记 provisional。完成表示该协议观察时间已结束，后续接受的合法更正仍可生成新报表。

元数据复用现有 marketing_report_snapshots，`013_marketing_reports.sql` 增量建立 JSON 内容表；快照元数据、内容和哈希同事务提交。历史引用不随退款或来源核对变化而被重写。页面支持下载当前已查询快照的 JSON。

## 估算接口

```json
{
  "ecpm": {"predicted_ctr": 0.1, "predicted_cvr": 0.2, "bid_minor": 1000, "bid_basis": "CPA"},
  "ltv": {"average_order_net_revenue_minor": 5000, "orders_per_month": 2, "lifetime_months": 12}
}
```

两个对象至少提供一个。CPC eCPM 不需要 pCVR；CPA 必须提供。概率限制在 0–1、金额整数分、生命周期 1–1200 月；输入使用两端共享 JSON Schema 校验。CPA 示例 eCPM=20000 分（200 元），LTV=120000 分（1200 元）。计算采用十进制有理数乘法，只在结果舍入六位小数。

结果标记 input_source=user_supplied_assumptions、status=estimate，保留输入假设和公式。LTV 是收入口径的估算，不是用户全生命周期实际收入或利润。没有接入 pCTR/pCVR 预测模型、完整客户身份链路、外部 RTB、二价扣费或真实资金结算。

## 验证与边界

固定案例：100 曝光、10 点击、2 个订单、1000 分费用、5000 分净收入，得到 CTR 10%、CVR 20%、CPA 500 分、CPC 100 分、CPM 10000 分、ROAS 5。费用必须先由全部来源核对完整。

单次报表最多 31 天、1000 个覆盖标记/证据分区，SELECT 设置 3 秒执行上限，金额/计数超出精确整数范围明确拒绝。当前按原始窗口事实和已发布关联计算，不宣称海量原始数据查询性能。大范围后台异步导出、Agent 报表工具和 S1 整体对账仍属后续任务；未将 CP05/CP06 整体勾选完成。

本地验证见 [业务指标验收记录](validation/marketing/business-metrics.md)。
