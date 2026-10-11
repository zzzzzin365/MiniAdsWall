# 广告属性召回：实现与运行

日期：2026-10-10。依据：[召回 SDD](ads-recall-sdd.md)。当前交付是单物理实例、32 个逻辑分区的可运行实现。功能验证与容量结果见 [验证记录](ads-recall-validation.md)。正式千万规模的数据库、读写混合与多节点容量验收仍未通过。

## 已落地代码

| 能力 | 代码 |
| --- | --- |
| 属性、查询、缺失字段、时间和数量校验 | `server/recall/contracts.ts` |
| 整数映射、值字典、属性关系、分区水位、事件与租约 | `server/migrations/002_ads_recall.sql`、`server/scripts/migrate-recall.ts` |
| 广告 CRUD/点击与 outbox 同事务；回执随幂等结果保留 | `server/models/ads.model.ts`、`server/services/recall/repository.ts` |
| Roaring 2.7.0、IN/NOT IN、分数分桶、不可变排名与文档版本 | `server/recall/partition.ts`、`tree.ts` |
| 4 个 Worker 各拥有 8 分区、全局排名合并、查询预算 | `server/recall/worker.ts`、`engine.ts` |
| 轮询、租约 fencing、持久检查点、恢复、在线重建 | `server/recall/server.ts`、`storage.ts`、`manifest.ts` |
| 有界候选、数据库详情复核、分页与有限 Agent 上下文 | `server/services/recall/search.ts`、`server/routes/ads.routes.ts` |
| 分页加载，编辑和复制前读取完整正文 | `client/src/api.ts`、`client/src/App.tsx` |
| 新旧 Agent 输入读取、样本范围、幂等快照语义 | `hosting/api.py`、`repository.py`、`pipeline.py`、`api/main.py`、`mcp/ads_tools.py` |

表中的 `server/`、`client/` 相对 `apps/mini-ad-wall/`。旧 `GET /api/ads` 仍保留兼容；页面、托管网关和同步助手使用有界读取。

## 部署顺序

需要 Node 22、MySQL 8.4，以及可安装原生 `roaring` 的运行环境。本次仅验证 macOS arm64，Linux 镜像安装尚未验证。仓库 `.env.example` 提供变量名；独立 Node 进程需要实际导出这些环境变量，不能假定 Python 的 dotenv 会配置 Node。

1. 备份数据库并停止旧版本广告写入口。配置所有 Koa 和召回实例连接同一个 `ADS_MYSQL_URL`。
2. 在 `apps/mini-ad-wall/server` 执行：

```sh
npm ci
npm run build
# 未迁移旧 JSON 时先执行；source 必须指向真实旧数据。
npm run migrate:ads -- --source /absolute/path/to/legacy-ads.json
npm run migrate:recall
```

已有业务表也必须完成基础 schema 迁移。`migrate:recall` 使用数据库迁移锁、SQL 校验和及可重跑的映射回填。已有广告初始 `enabled=false`，不会自动加入投放候选。不要直接修改已执行的迁移 SQL。

3. 设置召回进程和 Koa 的环境变量：

```sh
export ADS_RECALL_URL=http://127.0.0.1:3010
export ADS_RECALL_TOKEN='<至少16字符的内部凭据>'
export ADS_RECALL_CONSUMER_ID=recall-host-1
export ADS_RECALL_SNAPSHOT_DIR=/absolute/path/to/durable/recall-snapshots
export ADS_RECALL_ENABLED=true
```

运营凭据使用已有 `ADS_OPERATOR_TOKEN` 或 `ADS_OPERATOR_TOKENS`，与内部凭据分开。凭据不发给浏览器。运行 `npm run recall`，健康检查通过后恢复新版 Koa 写入口。生产应由进程管理器运行编译后的 `dist/index.js` 与 `dist/recall/server.js`。

4. 全部 Python API/Worker 部署兼容读取代码后，Koa 与 Python 同步设置 `AD_CONTEXT_V1_ENABLED=true`，重启进程。默认关闭新版上下文写入；关闭时托管网关和同步助手仍只发送最多 50 条广告样本。筛选条件需要开启新版协议和召回入口。

本次没有修改运行中的业务 `.env`，没有对业务数据库执行迁移或切换。

## 请求示例

通过已有广告创建接口提交业务字段，并额外传入：

```json
{
  "attributes": {"region": ["上海"], "category": ["数码"], "tags": ["d", "e"]},
  "eligibility": {"enabled": true, "starts_at": null, "ends_at": null}
}
```

默认登记字段为 `region/category` 单值、`tags` 多值。首次迁移可以执行 `npm run migrate:recall -- --fields /absolute/path/to/fields.json`，提供最多 128 个字段的目录；示例见 `server/recall/fields.example.json`。重复迁移须使用相同目录，已安装后改变目录会被拒绝，需要新版本迁移和重建。未知字段拒绝写入。`attributes` 整组替换，未提交则保留；`eligibility` 按提交的子字段修改。更新仍须带当前 `version` 和幂等键。

运营认证后请求 `POST /api/ads/search`：

```json
{
  "conditions": [
    {"field": "tags", "op": "in", "values": ["d", "e"]},
    {"field": "tags", "op": "not_in", "values": ["a", "b", "c"], "missing": "exclude"}
  ],
  "limit": 50,
  "min_receipts": []
}
```

含 `d/e` 的任一值且不含 `a/b/c` 的任何值才通过。`NOT IN` 默认排除缺失字段；显式 `missing=include` 才允许缺失。单字段多值不会用单值逻辑优化。

创建、更新、点击和删除产生 `recall_receipt={shard_id,seq,index_revision}`。序号与 revision 为十进制字符串。需要读取刚写入的广告时，把回执放入 `min_receipts`；100 ms 内未追上返回 503 `index_not_caught_up`，调用者可重试，不能把它当成成功的空结果。

列表使用 `GET /api/ads/page?limit=50&cursor=...`，返回 `items/next_cursor`。游标签名绑定运营身份，最长有效一小时。编辑和复制调用 `GET /api/ads/:id` 读取完整正文；列表仅返回 800 字符正文、最多 2 个视频预览和真实 `video_count`。

## 一致性与资源上限

写入锁顺序为幂等记录 → 定位映射 → 分区序号行 → recall_docs → 广告行 → 属性/事件/审计。点击没有幂等记录。创建先插入本事务的新映射。事务失败连同 seq、属性字典和事件一并回滚。

32 分区用 `doc_id % 32` 路由，分区内用 `floor(doc_id/32)` 作为位图 ID。分区 seq 持锁分配，消费必须连续，旧 revision 不覆盖新文档。删除保留墓碑。Worker 在一个消息内同步应用批次；查询保留候选位图与不可变文档/排名根，后续更新不会改掉已有候选的快照。请求完成、失败、取消或过期时主动释放临时位图容器和查询句柄，不依赖原生 GC 回收大块内存。

索引最终一致。详情阶段在短 MySQL 只读快照中再次检查当前属性、启用和时间，防止陈旧候选被返回；新广告尚未入索引时仍可能漏召回。排序来自索引快照，响应同时提供 `indexed_score/current_score`。不同详情批次可以来自不同数据库时间点。

查询最多 16 条条件、200 个值、100 条结果。单分区执行 50 ms，临时位图预算 8 MiB，宽候选先用分数分桶位图跳过不匹配区间，桶内保持精确排序，累计最多遍历 20,000 个排序节点；Koa 截止时间 200 ms，最多复核 2,000 条候选。临时预算采用容器字节数加保守原生操作预留，不等于进程精确 RSS。服务最多 32 个执行请求、64 个等待请求，等待超过 100 ms 拒绝。超限返回 422/429/503/504，不返回伪完整结果。

上下文最大 64 KiB，正文和视频明细省略。总体聚合尚未实现，明确输出 `summary=null, summary_status=not_materialized`；Python 工具标注样本范围，不把样本总点击/均价当作全广告统计。

## 恢复与运维

启动从消费者的 durable manifest 恢复，再回放连续事件；无检查点时读取同一个 MySQL 一致性快照，流式构建基线。快照是带 SHA-256 的紧凑文档 NDJSON，恢复时重新构造位图和排名树。每 60 秒保存增量事件文件，最多 30 层后转为完整快照。文件及 manifest 先 fsync，再登记数据库引用，目录也同步落盘。快照目录必须是持久存储，不能放容器临时层。

消费租约 15 秒、每 3 秒续租，检查点登记带 lease_epoch 条件。消费失联、序号断档或最新水位不可确认时拒绝查询。`ADS_RECALL_REBUILD=true` 让下次启动忽略旧快照、重新构建；正常情况下保持 false。

内部凭据认证的接口：

- `GET /health`：ready、32 分区水位、积压、内存、检查点/重建状态。
- `GET /metrics`：请求状态计数、ready、RSS、积压年龄、检查点年龄、活动/等待/句柄数、分区水位。
- `POST /rebuild`：后台生成新索引，追平并持久化后切换；健康接口报告失败原因。旧查询最多保留 5 秒。

`npm run prune:recall` 是显式清理命令。仅删除超过 24 小时，且全部消费者和回滚保护快照均已持久覆盖的事件；清理前校验恢复链的全部文件和 checksum。无持久水位、文件损坏或其他消费者目录不可读时不越过安全水位。

默认 outbox 预算 16 GiB，按 32 分区分配，单分区达到 90% 后拒绝业务写入并回滚。需要定期运行清理并监控使用量。当前不自动退役离线消费者、不自动删除历史快照文件；停用实例需要显式确认不再恢复后处理其消费者记录。多消费者清理需要共享可读的快照目录。

## 尚未达到的正式验收

没有实现跨物理节点的分区 RPC 路由、对象存储快照、完整聚合/异步报告和自动快照 GC。原生调用不可被 JS deadline 中途打断；最坏值分布和并发内存仍需压测。百万/千万 MySQL 关系表、F=100 高属性、100,000 个值、30 分钟开放式流量及混合读写、Linux 安装、真实多节点故障与切换仍待验证。单机索引测量不能证明 SDD 的 1,000 QPS 目标。
