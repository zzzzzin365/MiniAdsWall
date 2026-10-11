# 营销 v1 接入合约（CP00–CP04）

当前交付：最小资源、展示/曝光/点击、支付/退款、费用账本、来源核对，以及 CP04 独立 Worker 的归因、聚合和重建。已交付 31 天内报表、不可变快照与 eCPM/LTV 假设计算器；大范围异步导出仍待完成。详见 [报表协议](marketing-reports.md)；运行与容量边界见 [聚合运行说明](marketing-aggregation.md)。

共享格式位于 [schema.json](../contracts/marketing/v1/schema.json) 和 [examples.json](../contracts/marketing/v1/examples.json)。Koa 用 Ajv，Python 用 jsonschema 校验同一份文件；不自动转换字符串、数字或布尔值。ID 最长 100 位 ASCII；金额是整数人民币分，单条绝对值不超过 9007199254740991；时间必须为 UTC `Z`，最多毫秒精度。额外字段拒绝。

## 身份与入口

接口前缀 `/api/marketing`；`MARKETING_EVENTS_ENABLED=true` 才开放。其余四个阶段开关仍不可开启。

| 入口 | 身份 | 请求 Schema | 结果 |
| --- | --- | --- | --- |
| POST /campaigns、/creatives | 运营 + 品牌 membership + Idempotency-Key | CreateCampaign、CreateCreative | 新建草稿 |
| POST /campaigns/:id/status、/creatives/:id/status | 同上 | CampaignStatus、CreativeStatus | 状态与版本检查 |
| GET /campaigns、/creatives、/:id | 运营 + 品牌归属 | brand_id、cursor、limit | 默认 50、最大 100 |
| POST /serve | 消费者；服务端设置匿名签名 Cookie | ServeInput | 保存 decision 后返回展示凭证、素材入口、点击入口 |
| GET /media/:token | 同一匿名 Cookie + 展示凭证 | 路径 token | UPLOAD_DIR 下登记文件 |
| POST /events/impressions | 同一匿名 Cookie + 展示凭证 | ImpressionInput | 201 有效，202 隔离 |
| GET /click/:token | 同上 | 路径 token | 一次 click_id；302 到登记链接 |
| POST /conversions | 指定来源服务凭据 | ConversionInput | 201 有效；202 待支付/隔离 |
| POST /spend-entries | 指定费用来源服务凭据 | SourceSpendInput | 201 账本；202 隔离 |
| POST /spend-coverage | 同来源核对权限 | SourceCoverageInput | 201 匹配；422 核对失败并保留失败标记 |

服务凭据从 `MARKETING_SOURCES` 读取，以 `Authorization: Bearer ...` 传入；每项绑定名称、环境、brand_ids、interfaces。凭据至少 24 个字符，不能重复或复用运营凭据。来源内部键带环境后缀，例如 `shop_synthetic_conversions`、`shop_synthetic`，避免同名来源换环境后回放旧数据。请求不允许写 source/environment/principal。

服务返回统一 `{error:{code,message,request_id}}`。400 格式错误；401 无身份；403 范围错误；404 未启用/无可投素材；409 同键换内容/状态冲突；410 凭证过期；422 超额退款、更正或核对失败；503 存储/签名配置不可用。账本、事件、验证修订、接收回执和 outbox 原子提交，提交失败不确认成功。

## 展示协议

签名密钥 `MARKETING_SIGNING_KEY` 至少 32 字节，多实例共享。使用 HMAC-SHA256；v1 展示凭证 600 秒，匿名 Cookie 30 天，落地页 `miniads_click` 凭证 37 天。密钥轮换使旧凭证失效。Cookie 为 HttpOnly/SameSite=Lax，HTTPS 时 Secure。点击、素材接口及请求体日志隐藏凭证；返回 no-store/no-referrer。

凭证绑定环境、会话和不可变 decision；decision 保存 Campaign、素材版本和已登记链接。服务端不接收任意跳转目标或伪造会话。S1 使用 legacy 排序；活动暂停后已签发凭证在 TTL 内仍按原 decision 使用。

消费页地址 `/?campaign=<campaign_id>`。素材 `asset_ref` 是 UPLOAD_DIR 内相对文件路径；文件经签名 `/media/:token` 提供。浏览器使用原生 IntersectionObserver：至少 50% 且页面连续可见 1000 毫秒才发送曝光。滚出阈值、隐藏、卸载会取消计时；失败最多重试 5 次，保留同一 event_id 和 occurred_at；凭证过期后用户刷新获取新机会。重挂载复用事件状态，数据库按 impression_id 最终去重。客户端上报只能证明页面测量，不能证明真实人类流量。

同一来源事件 ID 同内容返回原回执，换内容 409。同一 impression 的不同上报 ID复用首个有效曝光。点击可先于曝光；同一 impression 跨进程只生成一个 click_id。新页不调用 legacy 点击累加，不修改旧 clicks/price/version。

## 订单与时间

v1 固定允许未来 5 分钟、延迟 30 天；以数据库 UTC 时钟计算。营销连接将 MySQL 会话临时设置为 UTC，归还前恢复；资源写入显式使用 UTC_TIMESTAMP，避免 DATETIME 默认值随服务器时区漂移。超出窗口保存为 quarantined，返回 202，不写有效支付/退款累计值。校正需使用新来源事件 ID，保留原隔离证据。

支付唯一业务键是内部来源、environment、order_id。支付重复换事件 ID但经济字段完全相同，复用已存回执；金额、时间、商品、点击关联不同返回 409。退款用独立 source_event_id，按订单行锁串行检查累计退款不得超过支付。

退款先到时返回 202/pending。支付到达后，按退款 occurred_at、内部事件 ID顺序验证：可容纳的退款追加验证 revision=2/valid，超额追加 revision=2/quarantined。原始事件及初次回执保持原值；退款有效累计值与验证 outbox 同事务更新。消费 Worker 必须读取 `marketing_event_validation` 的最新 revision，不能只读原始事件的 validation_status。CP04 Worker 已据此建立归因结果与聚合。

订单可省略 click_token，保留未关联订单；有 token 时必须验签并匹配服务端 click 记录、品牌、商品、环境。签名会话来自服务端记录，客户端不能补一个裸 session_id。

## 费用与完整性

费用事件独立于旧 price。source_entry_id 在环境限定的来源内唯一。更正追加流水，用 adjustment_ref 引用同来源、活动、素材版本的原流水内部 ID；必须保留原发生时间，更正后的该笔净费用不能为负。绝不覆盖原记录。接收时间可从 ingestion receipt 追溯。

活动首次费用接入/核对时，冻结配置中该品牌、环境全部声明 `spend-entries` 权限的来源；缺任何来源的 marker 都是 incomplete。不是从当前已收到的流水反推来源数量。改变来源清单返回 409，需要显式后续版本流程。

marker 含 source_marker_id、campaign_id、start、end、entry_count、amount_minor、checksum，区间 `[start,end)`。同 marker ID重复返回初次回执；重新核对用新 ID和 revision。金额为非负净总额，超出安全整数范围不能标记完整。

checksum 固定为 SHA256：按 source_entry_id 的 UTF-8 字节升序；每行对象仅含 source_entry_id、creative_id、creative_version、occurred_at、amount_minor、currency；UTC 时间规范到毫秒，键按字典序 JSON 编码，每行末尾一个 LF；空集合 SHA256 空字符串。独立实现不得用数据库返回顺序计算摘要。

新流水、更正或隔离费用使覆盖该发生时间的旧 complete 标记 invalidated。有未处理隔离费用的区间拒绝标记完整，即使有效账本的数量/金额/摘要已匹配；隔离修复仍需后续显式核对流程。核对失败返回 422，但仍提交 incomplete revision、失败回执、outbox，并使同区间旧标记失效。返回字段 `coverage_status_at_acceptance` 是接收当时的状态，`receipt_semantics=acceptance_snapshot`；重放回执不表示当前完整性。CP05 报表需按最新 revision/status 重新计算。

## 迁移与运行

新库：010_marketing_events.sql → 011_marketing_ingestion.sql → 012_marketing_aggregation.sql，沿用广告数据库命名锁；`up` 可重跑、失败可续跑，校验 SQL checksum、字段、索引、外键和 CHECK。`status` 仅读取，不初始化空库。营销迁移不会导入旧广告或补造历史营销事件。

历史 003_marketing_events.sql 原样存于 `migrations/legacy/`，只用于已有 v3 记录的校验与续跑。已有 applied v3 记录及原 checksum 不删除，升级新增 v10/v11/v12；新库不应用历史 v3。003_request_protocol.sql 属于另一迁移登记，继续保留。

```bash
export PATH=/opt/homebrew/opt/node@22/bin:$PATH
npm run migrate:marketing --prefix apps/mini-ad-wall/server -- status
npm run migrate:marketing --prefix apps/mini-ad-wall/server -- up
npm run seed:marketing --prefix apps/mini-ad-wall/server -- --help
npm run seed:marketing-events --prefix apps/mini-ad-wall/server -- --help
```

先配置 ADS_MYSQL_URL，再显式播种品牌/商品/membership/广告归属，创建素材、审核并启用活动。营销入口默认关闭。消费、接入与聚合 Worker 要求迁移 12；资源要求迁移 10。升级先停止旧营销写入进程，再迁移并启动新版；旧版本缺少输入时钟，不能与 CP04 Worker 混跑。

synthetic 生成器仅允许本机 HTTP、synthetic/sandbox，并先核对远端 serve 环境；凭据通过 MARKETING_SEED_SOURCE_TOKEN 提供。固定 run-id 和 occurred-at 可重复运行；9 个案例覆盖乱序退款、支付/退款重复、超额退款、费用重复/改金额与覆盖 marker。配置两个费用来源且只运行一个来源时返回 missing_sources；模拟数据不代表真实营销收益。

验收报告：[CP00–CP03](validation/marketing/CP00-03.md)、[CP04](validation/marketing/CP04.md)。
