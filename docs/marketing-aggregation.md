# CP04 营销归因与聚合运行说明

实现位置：`marketing/aggregation/{repository,worker,attribution,metrics,rebuild}.py`，迁移 `012_marketing_aggregation.sql`。独立 Python 进程消费同一广告 MySQL；不依赖托管任务 Worker，也不读取/修改旧广告点击。

## 运行

升级时先停旧营销写入进程，执行迁移，再运行新版 Koa。旧版本不推进输入时钟，不能与新版营销 Worker 混用。010/011 内容不改，012 增量建表；013 增量添加报表内容存储；未来内容/执行/分发/学习迁移改为 014/015/016/017。

```bash
export PATH=/opt/homebrew/opt/node@22/bin:$PATH
npm run migrate:marketing --prefix apps/mini-ad-wall/server -- up
npm run migrate:marketing --prefix apps/mini-ad-wall/server -- status

# 从私密环境配置加载 MARKETING_WORKER_MYSQL_URL；本地可另设 socket。
# 不回退到 ADS_MYSQL_URL 或 hosting DATABASE_URL。
.venv/bin/python -m marketing.aggregation.worker --environment synthetic --once
.venv/bin/python -m marketing.aggregation.worker --environment synthetic --drain
.venv/bin/python -m marketing.aggregation.worker --environment synthetic --status
# 常驻消费：省略 --once/--drain/--status，SIGTERM/SIGINT 结束。
```

生产、沙箱和模拟环境必须显式指定。入口开关默认关闭；Worker 显式启动后，即使入口关闭，也继续消费已接收任务。`--drain` 在没有当前可领取任务时退出，最多 1000 批；不意味着退避中/失败的任务已经处理完，要查看 status。

专用授权模板：`apps/mini-ad-wall/server/scripts/marketing-worker-grants.sql`。DBA 在文件外创建账号和密码，替换数据库名后授权。Worker 只有原始营销表读取、outbox 领取/更新和派生表写入权限；无原始事实写入、旧广告权限、outbox INSERT。输入时钟有单列 UPDATE 授权，因为 MySQL 的 `SELECT ... FOR UPDATE` 需要写权限。模板已用真实独立 Worker 账号验证。

## 领取和提交

每批最多 25 项、同品牌/环境；`FOR UPDATE SKIP LOCKED` 领取，记录 attempt、120 秒租约和递增 lease_epoch。5 次尝试或接收后 24 小时截止；临时失败按 2 秒到 60 秒退避。未知任务/坏引用进入 failed 并保留 error_code，其他合法任务继续处理。租约过期可重领，旧代次不能发布。

事件、最新验证 revision、费用和 outbox 在一个事务中推进 `(brand_id,environment)` 输入 epoch。epoch 描述已提交业务变化；不能用 MAX(outbox.id) 充当提交顺序，因为自增 ID 可能先分配后提交。

Worker 在短的只读一致快照事务内读取原始事实、最新校验、现有指针和 cutoff，再关闭事务进行计算。提交事务先锁输入时钟和发布 generation，检查快照是否仍有效，再检查任务租约/代次。归因历史、当前指针、点击订单关联、日分区历史、待重建标记、发布状态与 outbox done 一起提交。输入变化、其他 Worker 已发布或租约失效都回滚，不允许旧结果覆盖新结果。

当前每批对该品牌/环境做有界原始数据核对，事件按 received_at/id 排序，最新验证 revision 的 outbox 同样触发重算。仅内容发生变化的分区追加 revision，旧归因指针被替换，不累加重复转化。没有凭接收时间单独跳过迟到事实。每类输入/派生查询默认最多 100000 行；超限明确 failed，绝不截断后确认 done。该实现尚未做大量历史数据下的分区分页和吞吐压测。

每品牌/环境非 done 任务达到 10000 时，接入返回 503/marketing_backlog_full，事件/账本/回执/输入时钟全部回滚；failed 也占容量，不能用无限坏事件填队列。status 返回状态计数、最老年龄、失败原因、最新校验的隔离/待处理计数、隔离费用、待重建分区和阈值。失败任务保留供运营排查，不自动抹掉或无限重试。

## 归因和两套日期

当前支持 `last-click-v1`；其他规则版本拒绝，不假装执行未知规则。有效点击必须同品牌、商品、会话、环境，落在支付前 7 天到支付时刻的闭区间。取 occurred_at 最晚的点击；同时刻取 click_id 字节顺序较大的项。支付会话来自 CP03 服务端已验签证据，缺会话保留 session_missing，无匹配保留 no_click_in_window。待支付退款保留 pending。Worker 读取最新 `marketing_event_validation`，不以原始行的 pending 值排除已经重新验证的退款。

有效部分退款只扣一次，累计不得超过支付；净收入和退款订单数回写原支付日。有至少一笔正金额有效退款的订单计一次 refunded_orders；零元退款不增加该计数。归因的收入/支付订单记入所归因素材的支付日；未归因收入保存在订单归因结果，不能挤入某素材的广告收入。

`marketing_daily_metrics` 是按 Asia/Shanghai 日界的曝光、点击、支付订单、退款订单、净收入和费用。`marketing_metric_heads` 指向当前分区 revision，历史行保留。`marketing_click_orders` 按 source/order/environment/rule 唯一，另存 click_at、paid_at 和归因 revision，且有点击时间索引。点击人群 CVR 的分子来自该关联，不能用同日 paid_orders / clicks 替代；一次点击可以归因多笔订单。

迟到点击导致归因素材改变时，重算旧素材与新素材分区，包括写入零订单/零收入替换；退款、费用更正也重新核对原事件日。自动修订今天及之前 29 个上海自然日；接收允许未来 5 分钟跨日时包含该日。更早的变化只更新订单归因并标记 `marketing_pending_partitions`，原日报保持旧 revision，直到显式重建。

## 显式重建

以具备调度权限的运营数据库账号运行下列 CLI，临时配置该账号的 MARKETING_WORKER_MYSQL_URL；消费进程仍使用专用 Worker 账号。CLI 是后台管理命令，`--actor` 为操作记录，不能替代数据库认证。范围为上海自然日 `[start,end)`，每任务最多 366 天。

```bash
.venv/bin/python -m marketing.aggregation.rebuild \
  --brand brand_a --environment synthetic --rule last-click-v1 \
  --start 2026-09-01 --end 2026-10-01 --actor operator --request-id rebuild-september
.venv/bin/python -m marketing.aggregation.worker --environment synthetic --drain
```

任务和 outbox/输入 epoch 原子创建；同 request-id 同参数回放，同 ID 换参数失败。新任务 ID 可再次重建并保留新分区 revision。按原始事件及最新校验、费用账本全量计算，再替换指定范围的日分区，清除对应待重建标记；同步处理当前自动窗口的变化。归因内容不变时不追加重复归因 revision。

`marketing_rebuild_jobs` 保存请求范围/环境/规则/操作人、完成状态、原始行数与输入摘要、全品牌归因净收入/费用核对总额和指定范围指标总额。只替换派生结果，不修改原始事实。新增合法事件仍可触发后续 revision，重建结果不宣称永久完整；当前报表口径、观察状态和证据快照见 [业务指标与报表](marketing-reports.md)，大范围异步导出仍待实现。
