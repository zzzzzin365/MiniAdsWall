# 广告属性召回验证记录

日期：2026-10-10。关联：[SDD](ads-recall-sdd.md)、[实现与运行](ads-recall-implementation.md)。当前验收结论：单实例功能通过，正式容量验收未完成。

## 环境和范围

macOS arm64，Node 22.23.2，10 核、32 GiB 内存，原生 roaring 2.7.0。MySQL 集成测试每次初始化临时 MySQL 8.4 数据目录和 Unix socket，测试后清理，未连接或修改业务数据库。Python 使用项目 `.venv`。

## 已验证

| 测试 | 结果 | 覆盖 |
| --- | --- | --- |
| Node 召回及关联回归 | 29/29，通过，无跳过 | MySQL CRUD、业务认证/幂等、托管网关、召回/恢复、协议和迁移 |
| Python 关联回归 | 39/39，通过 | 有限上下文、工具样本范围、首次幂等快照、历史输入、托管状态及聊天预检 |
| 前端构建 | 通过 | TypeScript + Vite 打包；不等于浏览器交互验收 |
| 完整随机差分 | 6/6，通过 | 100 固定种子 × 每种子 10,000 文档 × 200 查询，结果逐项等于扫描参考实现；另含合约/版本/预算、60,000 文档宽候选和失败句柄回收用例 |

差分测试为了完整枚举参考结果不施加 CPU 截止时间；生产仍为每分区 50 ms，生产预算拒绝在独立用例和容量测量中验证。因此它证明集合和排序正确性，不证明这些随机查询全部能在生产预算内成功。

真实 MySQL 用例验证：默认/自定义字段目录迁移重跑及目录变更拒绝、业务与事件同事务回滚、容量耗尽时字典/映射/seq 一并回滚、32 分区水位、IN+NOT IN 排除多值冲突、未知字段拒绝、分页游标、删除墓碑、写回执等待及不可达回执 503、重启恢复、在线重建切换持久 generation、消费者重复启动拒绝、metrics；另验证完整/增量检查点恢复、checksum 损坏时拒绝清理、无持久水位不清理、24 小时回滚保护、可安全删除事件后仍能恢复、重复分区 manifest 拒绝。

上下文用真实数据库的 101 条广告、5,000 字符正文和 3 个视频，验证服务端选取 50 条、64 KiB 上限、丢弃浏览器伪造广告、无正文、真实视频数量、同步/托管两个入口均有界、总体 summary 明确未物化。网关上游为捕获服务；不把它算作真实模型执行。

证据：`docs/evidence/ads-recall/scoped-node.log`、`python.log`、`client-build.log`、`qualification.log`。

## 索引层容量测量

生成的数据每条 10 个属性值、2 个字段，值数量上界 9,010。合成 ID/分数分布固定、95% enabled；查询是单值 IN 加多值 NOT IN，limit=50，顺序执行 200 次。未按 SDD 的 100,000 个值、F=100、混合分布和开放式到达率压测。

| 行数 | 成功/请求 | P95（成功请求） | P99（成功请求） | RSS | 索引构建 |
| --- | --- | --- | --- | --- | --- |
| 1,000,000 | 200/200 | 4.22 ms | 4.63 ms | 1,354,235,904 B | 6.53 s |
| 10,000,000 | 200/200 | 13.39 ms | 19.47 ms | 7,931,084,800 B；峰值 9,519,022,080 B | 216.06 s |

结果来自分数分桶和查询句柄修复后的索引版本。JSON 如实保留拒绝数，百分位只计算成功请求。每次请求包含 Worker 位图过滤与全局排名合并，不包含 MySQL 属性关系表、详情读取、Koa、HTTP、并发更新和快照恢复成本。

高属性 smoke：10,000 行、F=100、值上界 99,010，20/20 成功，索引层 P95 2.79 ms、RSS 421,019,648 B。它仅验证小数据的高属性构建/查询路径，不证明千万 F=100 容量。见 [原始结果](evidence/ads-recall-f100-smoke.json)。

原始结果：[百万](evidence/ads-recall-million-index.json)、[千万最终复测](evidence/ads-recall-ten-million-index.json)、[千万首次失败](evidence/ads-recall/ten-million-first-failed.json)。千万首次测试 0/200 成功，87 次预算拒绝、113 次过载，暴露排名与属性相关时连续空扫描和失败句柄滞留；这两项已修复，并新增 60,000 文档宽候选比对及连续 40 次失败后正常查询的回归。分桶后第二次千万测试 112/200 成功、2 次预算拒绝及 86 次截止时间超限，记录保留 `ten-million-bands-before-dispose.json`；随后增加查询结束/取消时主动清空原生临时位图，包含未启动迭代器的回收验证。这是索引层可行性证据，不能推导完整系统的 QPS 或千万级生产 SLA。

## 未通过与未运行

完整差分复验曾因 1 秒测试预算中断，5/6 通过，原始记录保留 `qualification-bounded-failed.log`；不把该次运行当作成功。

Node 全库修复前记录 34 项测试：28 通过、6 失败。其中召回上下文视频计数类型及惰性生成器预算断言两项已修复，并在 29 项范围回归中通过；剩余 4 项为营销模块的 AJV strictRequired 与 marketing_schema_check_mismatch，仍未解决。原始失败记录保留 `full-node-before-fixes.log`，没有把全库结果写成通过。

SDD 第 15 节正式容量门未执行：百万 500/s、千万 1,000/s、30 分钟开放式流量、属性/点击混合写、千万 MySQL/F=100、生产 Linux 镜像、四物理节点、网络故障、重建期间压测。最终的千万 200 次串行索引查询均成功，只证明本次合成分布在单机索引预算内可运行。分区锁高点击争用、详情查询 EXPLAIN 和完整内存分项尚未测量。未部署和切换业务环境。

## 重跑

在 `apps/mini-ad-wall/server`，使用 Node 22：

```sh
npm run build
node --test --test-concurrency=1 tests/ads-mysql.test.cjs tests/business-boundary.test.cjs tests/hosting.test.cjs tests/recall*.test.cjs tests/schema-migrations.test.cjs
RECALL_TEST_SEEDS=100 RECALL_TEST_DOCS=10000 RECALL_TEST_QUERIES=200 node --test tests/recall.test.cjs
npm run benchmark:recall -- 1000000 200 /absolute/path/to/million.json
# 生成 F=100 高属性数据；每个字段最多 50 值。增加规模前评估 RSS。
ADS_RECALL_BENCH_ATTRIBUTE_VALUES=100 npm run benchmark:recall -- 10000 20 /absolute/path/to/f100.json
# F=10 时设每位置基数 11,110，得到 100,000 个值的上界。
ADS_RECALL_BENCH_TERM_CARDINALITY=11110 npm run benchmark:recall -- 1000000 200 /absolute/path/to/t100k.json
ADS_RECALL_BENCH_MAX_RSS_BYTES=17179869184 npm run benchmark:recall -- 10000000 200 /absolute/path/to/ten-million.json
```

容量测量与定时预算测试串行运行，避免相互争抢 CPU。RSS 预算超限时 benchmark 退出非零，不把提前停止写成成功。MySQL 集成测试缺少真实 mysqld 时明确 skipped；本次没有 skipped。

仓库根目录：

```sh
.venv/bin/python -m unittest tests.test_ads_recall_context tests.test_hosting_api tests.test_hosting tests.test_chat_preflight -q
```
