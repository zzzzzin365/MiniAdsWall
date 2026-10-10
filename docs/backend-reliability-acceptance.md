# MiniAdsWall 后端可靠性修复：阶段验收记录

日期：2026-10-10。依据：[SDD](backend-reliability-sdd.md)、[Coding Plan](backend-reliability-coding-plan.md)。

CP01、CP02 的实现与阶段验证已完成。CP03–CP28、正式双宿主 HA、容量和灾备验收仍未完成。下列基线复现不是 AC01 的修复通过。

## 1. 基线与环境

- 基线 HEAD：`8ff5e2a7a7eeda384406de0f0e1a84e627638774`，工作区已有未提交改动。初始状态、差异摘要和差异 SHA-256 见 [工作区基线](evidence/backend-reliability/cp01-worktree-baseline.json)；HEAD 本身不能代表此次测试的完整源码。
- 单宿主 macOS arm64；Python 3.11.15、MySQL 8.4.11、Redis 8.10.0。MySQL 使用脚本创建的临时实例/数据库，Redis 使用测试 Unix socket。未迁移实际业务数据库。
- 初次 Koa 基线使用 bundled Node 24.19.0；最终 Koa 验证使用 Node 22.23.2，匹配工作区 Roaring 原生模块 ABI 127。系统 Node 25 缺少动态库，首次启动失败已保留记录；没有修改系统安装。
- Koa 上游捕获服务和可控 Worker pipeline 用于验证请求与状态，不代表真实模型或 Docker 结果。跨进程竞争均在同一宿主，不能据此宣称跨机器高可用。
- 工作区同时新增召回和营销模块。本次保留这些改动；其测试结果单独标明，不把新增模块能力算作 CP01/CP02 的交付。

## 2. CP01：复现与当前回归

| 验证 | 实际结果 | 证据 |
| --- | --- | --- |
| FastAPI 旧输入 1,000/1,001 条边界 | 1,000 条返回 202，持久输入保留 1,000 条；1,001 条返回 422/too_long，未创建第二个 run | [边界日志](evidence/backend-reliability/cp01-input-boundary.log)、`tests/test_hosting_api.py` |
| Koa 旧生产者是否发送全量广告 | 真实 MySQL 中放入 1,001 条，HTTP 捕获服务收到相同全部 ID；伪造 ads 被覆盖 | [网关日志](evidence/backend-reliability/cp01-koa-boundary.log)、`server/tests/hosting.test.cjs` |
| 原业务 MySQL、双 Koa 和客户端恢复 | 初始 19 项通过，0 跳过；增补数量断言后仍 19 项通过 | [初始 Koa](evidence/backend-reliability/cp01-koa-baseline.log)、[数量基线](evidence/backend-reliability/cp01-koa-boundary.log) |
| 原托管回归 | 36 项，31 通过、5 skipped；缺真实 Docker 运行条件和专用 MySQL 测试变量 | [初始托管日志](evidence/backend-reliability/cp01-hosting-before.log) |
| 独立 MySQL 多进程竞争与 Worker SIGKILL | 六项均通过；SIGKILL 后 20.316 秒收敛，旧结果拒写、检查点可显式 resume | [本次基线 JSON](evidence/backend-reliability/cp01-mysql-baseline.json) |

网关捕获与 FastAPI 接受/拒绝是两段独立验证，没有把捕获服务冒充完整 Koa→FastAPI 实网链路。AC01 的正式目标还需要 CP04–CP07 的有界上下文和 100,000 条数据验收。

## 3. CP02：已实现内容

### 3.1 增量迁移

托管库新增 `hosting_schema_migrations` 和 `hosting_migration_attempts`，记录版本、文件/冻结初始 schema 的校验值、执行批次、状态、时间及脱敏错误类型。

- v1：冻结初始 schema，可初始化新库或接管旧 `create_all` 库，不删除业务数据。
- v2：增量增加 `agent_runs.input_protocol_version` 和 `tool_calls.executor_protocol_version`，可空且默认 1；旧记录的 NULL/1 均按旧协议处理，输入对象、请求指纹和历史不重写。
- 旧 `schema_versions` 保持单行 v1。增量版本写入新账本，兼容旧 API 的 `scalar_one()` 健康检查。
- MySQL 用数据库级具名锁串行执行；文件 SQLite 测试用文件锁。状态命令只读；校验值、未知迁移版本、错误列结构或降级请求明确失败。
- DDL 执行前提交 applying；正常失败记录 failed，进程直接退出可保留 applying。重试先检查实际表/列，再继续尚未完成的步骤；每次尝试单独留痕，不宣称 DDL 可以事务回滚。
- 托管服务启动只检查迁移状态，不执行 DDL；迁移命令不依赖对象存储、cursor secret 或模型客户端。

业务库新增 `ads_schema_migrations`、`ads_migration_attempts`，同样记录版本、校验值、批次和执行结果，沿用旧导入器的数据库迁移锁。

- `001_ads.sql` 原文件不变，旧 `schema-ads-v1` 校验标记仍检查。
- `003_request_protocol.sql` 给业务幂等操作增加可空、默认 1 的请求协议字段。002 属于召回模块，由其独立迁移入口管理，本命令不顺带执行召回迁移。
- `migrate:ads` 保留旧 JSON 验证和只读源文件语义，先调用显式 schema 迁移，再执行原导入事务。应用启动不调用该入口。
- SQL 分句保留字符串中的分号、跳过注释中的分号；不支持 DELIMITER/存储过程体和 MySQL 可执行注释，遇到这些格式明确拒绝。

### 3.2 协议与功能开关

两端统一配置 `BACKEND_DEPLOYMENT_MODE`、输入/执行器协议版本及五个开关，默认 development、协议 1、全部关闭。

| 配置 | 当前行为 |
| --- | --- |
| 输入协议、执行器协议 | 支持 1；显式未知版本拒绝。Worker 不领取数据库里不支持的输入版本；旧 NULL 可领取 |
| `AD_CONTEXT_V1_ENABLED` | 默认 false。工作区新增模块已提供上下文读取能力，开关可显式配置；启用写入仍须先完成全体 Worker 兼容升级和旧任务排空，本次未开启 |
| `SHARED_ASSET_WRITES_ENABLED` | 默认 false，true 明确报未实现 |
| `NARROW_TRANSACTIONS_ENABLED` | 默认 false，true 明确报未实现 |
| `SSE_SHARED_READER_ENABLED` | 默认 false，true 明确报未实现 |
| `DYNAMIC_EXECUTOR_ENABLED` | 默认 false，true 明确报未实现 |
| `multi_node` | 配置值已定义；当前启动明确拒绝，等共享存储、执行归属和部署能力完成再开放 |

Koa 两条上下文生产路径均受默认关闭的开关控制；旧路径仍读取服务端广告事实，剥离客户端伪造的 `ad_context`。新增可空字段的默认值不改变旧请求幂等指纹；普通输入重试与显式协议 1/空上下文/空条件的重试结果一致。

### 3.3 命令和回滚限制

先配置隔离或部署目标库的 `DATABASE_URL` / `ADS_MYSQL_URL`；执行迁移的账号需 DDL 权限。以下入口已实现：

```bash
.venv/bin/python -m hosting.migrate status
.venv/bin/python -m hosting.migrate up
.venv/bin/python -m hosting.migrate up --target 1
npm run migrate:schema --prefix apps/mini-ad-wall/server -- status
npm run migrate:schema --prefix apps/mini-ad-wall/server -- up
npm run migrate:schema --prefix apps/mini-ad-wall/server -- up --target 1
```

`--target 1` 仅供尚未执行后续版本的分步发布；已经升级后会拒绝降级。迁移状态/校验值检查失败不能绕过，已执行文件不能改写。服务启动不会代替上述命令。

本阶段新写入关闭时，回滚读取能力最低要求为：旧输入/执行器协议 1、共享业务 MySQL、原有任务配额和 fence 校验。新增字段保留，不能 DROP 列回滚。正式发布前须保存含既有未提交业务改动的兼容构建/镜像，不能只回退到基线 HEAD；本次基线差异哈希不是可恢复的源码备份。

新版上下文、执行归属或共享对象写入启用后，最低兼容版本必须随阶段提高，不能回滚到不理解该数据的旧 Worker。正式发布预检、备份恢复和跨版本滚动演练属于 CP24，仍 pending。

## 4. 验证结果和未通过项

| 范围 | 结果 | 证据 |
| --- | --- | --- |
| CP01/CP02 相关 Koa、业务 MySQL和客户端回归 | 以最终范围日志为准，必须 0 失败、0 skipped | [范围日志](evidence/backend-reliability/cp02-koa-scoped.log) |
| 完整 Python 测试 | 104 项，91 通过、13 skipped，0 失败 | [Python 日志](evidence/backend-reliability/cp02-python-verified.log) |
| 真实 MySQL 迁移专项 | 18 项全部通过，包含 8 项真实 MySQL、8 项文件 SQLite、2 项协议/开关验证 | [MySQL 日志](evidence/backend-reliability/cp02-mysql-verified.log) |
| 托管六项多进程/SIGKILL 回归 | 全部通过；最后一次恢复 20.295 秒，旧写入被拒绝、检查点恢复成功 | [机器可读结果](evidence/backend-reliability/cp02-mysql-verified.json) |
| 前端构建 | 通过 | [构建日志](evidence/backend-reliability/cp02-client-build.log) |
| 全量 Koa 测试，含同时新增模块 | Node 22：31 项，27 通过、4 失败；4 项均在新增 marketing.test.cjs，涉及契约 strictRequired 和 marketing_schema_check_mismatch | [全量日志](evidence/backend-reliability/cp02-koa-node22.log) |

迁移专项实际覆盖：空库 status 不写表、新库初始化、旧库接管及事实保留、重复执行、并发迁移串行、锁超时不写 schema、部分 DDL 完成后恢复、applying 状态恢复、尝试历史保留、校验值冲突、未来版本拒绝、降级拒绝、未知协议拒绝及旧记录领取。

Python 的 13 个 skipped 是 4 个真实 Docker 用例、8 个需要专用 MySQL 环境变量的迁移用例、1 个 MySQL 竞争用例。8 个迁移用例已在上述真实临时 MySQL 专项中另行全部通过；竞争由六项多进程脚本另行验证。Docker 仍未验证，不能计作通过。

Node 24 的全量测试还曾因新增 Roaring 模块的原生 ABI 不匹配失败；切至 Node 22 后召回测试可执行。保留失败日志，最终不把营销模块的 4 个失败隐去，也不因本阶段范围回归通过就称整个仓库全绿。

## 5. AC 状态

AC01：基线复现完成，修复验收 pending。AC18/AC19：迁移和协议子项已通过，备份恢复、发布中止和全体实例兼容演练 pending。其他正式 AC 均 pending。

本次没有生产数据库迁移、真实模型容量验证、跨宿主部署或自动故障切换演练。CP01/CP02 阶段完成，不代表 SDD 完整验收完成。
