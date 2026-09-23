# Agent 托管：实现与运行

2026-09-23：已实现独立 Worker、数据库生命周期、模型真实流式输出、SSE、受限 Docker 工具执行器和聊天框接入。**MySQL 8.4.11 已通过独立进程竞争与 Worker 崩溃恢复实测；Docker 隔离与十亿记录容量目标仍未验证。**

## 代码入口

| 能力 | 实现 |
| --- | --- |
| 数据模型 / 初始迁移 | `hosting/schema.py`、`hosting/migrate.py`、`migrations/001_hosting.sql` |
| 生命周期、租约、用户配额、游标分页、审批和审计 | `hosting/repository.py` |
| Redis 登录、请求限流、SSE 连接数 | `hosting/auth.py` |
| 独立控制面、SSE 重放、对象下载 | `hosting/api.py` |
| Worker、取消、超时、租约恢复、outbox | `hosting/worker.py` |
| 原有预检 / 广告工具 / 记忆历史的接入 | `hosting/pipeline.py` |
| OpenRouter / Anthropic 原生流式读取 | `hosting/streaming.py` |
| Worker 到执行器的认证请求 | `hosting/sandbox.py` |
| Docker 容器执行、独立清理 watchdog | `hosting/executor.py`、`hosting/sandbox_entry.py` |
| 本地共享对象目录 / S3 | `hosting/objects.py` |
| Koa 认证代理、真实 SSE 转发 | `apps/mini-ad-wall/server/routes/agent.routes.ts` |
| 会话选择、增量回复、取消、审批、恢复、历史加载 | `apps/mini-ad-wall/client/src/components/AIAssistantPanel.tsx` |

旧 `/chat` 和创意生成接口保留。聊天框现在使用 `/api/agent/*`；需要启动下面的托管服务。不会因为托管服务不可用而偷偷切换到一次性接口。

## 启动

在已有 `.env` 补齐 `.env.example` 中的变量，**保留已有模型密钥**。`HOSTING_CURSOR_SECRET` 至少 32 字节且重启不能变化。数据库密码放入连接 URL 时必须转义；Compose 示例建议使用随机字母数字密码。

无 shell 的托管服务：

```bash
docker compose -f docker-compose.hosting.yml up --build -d
```

此配置启动 MySQL、Redis、一次性迁移、API 和 Worker。API 仅绑定 `127.0.0.1:8002`；MySQL/Redis 不对宿主发布端口。API 与 Worker 共用对象卷。多宿主部署必须改为共享 S3，配置 `HOSTING_S3_BUCKET`、`HOSTING_S3_ENDPOINT` 和标准 AWS 凭据；不要使用每台机器独立的本地目录。

Koa 进程须配置相同 `AGENT_SERVICE_TOKEN`，设置 `AGENT_HOSTING_URL=http://127.0.0.1:8002`。运营凭据继续使用 `ADS_OPERATOR_TOKEN`；多用户可用 `ADS_OPERATOR_TOKENS={"alice":"…","bob":"…"}`。浏览器只能提交凭据，用户身份由 Koa 验证后确定，无法通过请求体指定别人的 user_id。当前这是开发用静态凭据映射，接入企业身份提供方仍属于部署工作。

```bash
npm start --prefix apps/mini-ad-wall/server
npm run dev --prefix apps/mini-ad-wall/client
```

这些环境变量必须提供给实际启动 Koa 的进程；Koa 不会自行读取根目录 `.env`。

使用已有 MySQL/Redis 单独启动各进程：

```bash
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m hosting.migrate
.venv/bin/python -m uvicorn hosting.api:app --env-file .env --host 127.0.0.1 --port 8002
.venv/bin/python -m hosting.worker
```

迁移是独立命令，不在每个 HTTP Worker 启动时执行。`migrations/001_hosting.sql` 是可审阅的初始结构，和 Python 迁移二选一；不能反复执行裸 SQL。代码只允许生产使用 MySQL URL；SQLite 必须显式 `test=True`，不会静默降级。

## 启用隔离工具

先构建固定工具镜像：

```bash
docker build -t miniadswall-sandbox:1 -f hosting/Dockerfile.sandbox .
```

设置 `HOSTING_EXECUTOR_TOKEN`、`HOSTING_EXECUTOR_URL=http://executor:8003`，并在 `ACTION_PERMISSION_MAP` 给指定身份赋予 `tools.shell`，随后：

```bash
docker compose -f docker-compose.hosting.yml --profile shell up --build -d
```

每个 Docker 宿主只部署一个 executor。只有该可信服务挂载 Docker socket；API、Worker 和工具容器均不挂载。工具容器无网络、非 root、根文件系统只读，CPU/内存/PID/文件描述符受限；workspace 和临时盘使用有大小上限的 tmpfs。默认镜像提供 Python 和 shell，不包含用户项目的全部依赖；需要测试某种技术栈时构建经过审核的工具镜像，再设置 `HOSTING_SANDBOX_IMAGE`。

工具任务使用 `POST /sessions/{id}/runs`，例如：

```json
{"message":"运行工作区测试","tool":"test","argv":["python","-m","unittest","discover"]}
```

这类任务会暂停等待审批，权限和参数哈希在恢复时再次校验。它是显式工具任务；目前广告 Agent 不会自行把自然语言转成任意 shell 命令。每个 run 同时只执行一个 shell，低于设计中最多两个的上限。

工作区可通过内网控制面 `PUT /workspaces/{id}/snapshot` 上传未压缩 tar，最多 32 MiB / 10,000 个普通文件与目录；路径穿越、符号链接和特殊文件被拒绝。run 从不可变快照恢复到私有 tmpfs，容器结束后将新的快照放入对象存储。`POST /runs/{id}/workspace/commit` 才会发布成功 run 的快照；版本不匹配返回 409，不覆盖其他 run 的结果。这两个管理端接口尚未添加聊天框操作入口。

run 取消和超时后先停止容器并确认清理，再释放名额；执行器故障时保留 stopping，避免虚报停止成功。租约过期先撤销旧 Worker 的写入资格，再清理容器。执行器 watchdog 独立于 Worker；容器内还有命令硬截止时间。

## 与原设计的明确差异

- 时间字段使用 MySQL `DOUBLE` 保存 UTC Unix 秒，公开 ID 仍是 BIGINT 字符串。不是低精度 `FLOAT`。
- 配额计数从事务内的已索引 run 状态读取；`runtime_capacity` 全局行锁串行化短状态事务，用户行记录最近调度时间用于公平调度。没有维护一份容易漂移的 running_count 副本。模型和工具不在该事务中运行。
- Redis 缓存最近上下文，按 session version 隔离；历史和审批始终由 MySQL 恢复。当前没有 session 列表缓存。
- 普通工具串行执行；同一用户最多两个 run、集群最多 16 个 run。单宿主 shell 容器最多 8 个。
- 模型总超时 120 秒、读取空闲超时 30 秒，同时受模型并发门的执行上限约束；Compose 为该门设置 125 秒。原 `/chat` 的 28 秒上限不用于新 Worker。
- shell run 遭中断后不允许自动重放；需要明确提交新命令。广告只读工具可从检查点复用，原 run 的终态不修改。
- `python -m hosting.cleanup` 分批清理过期 SSE 事件、临时待确认记录和已投递 outbox。永久历史归档、对象 GC、备份恢复、MySQL 副本、分片和十亿记录压测尚未完成，不会擅自删除永久历史或输出对象。
- run trace 包含排队、预检、工具、模型、首增量和运行耗时；尚未接入完整的分布式追踪后端及 token 用量报表。

## 验证边界

常规回归：

```bash
.venv/bin/python -m unittest discover -s tests -v
npm test --prefix apps/mini-ad-wall/server
npm run build --prefix apps/mini-ad-wall/client
```

新增测试覆盖：幂等创建、同 session 排他、跨用户越权、取消与成功竞争、旧租约拒写、名额上限、排队、分页游标、SSE 重放、超时清理、Redis 丢失后重新登录恢复历史、审批单次消费、真实预检/广告工具与模拟模型流的集成。

以下用例需要实际服务，默认明确跳过，不能算通过：

```bash
# 必须指向专用、可丢弃的测试数据库，不使用生产数据库。
HOSTING_TEST_MYSQL_URL='mysql+pymysql://…/hosting_test' .venv/bin/python -m unittest discover -s tests -p test_hosting_mysql.py -v
HOSTING_TEST_DOCKER=1 .venv/bin/python -m unittest discover -s tests -p test_hosting_docker.py -v
```

浏览器已在临时 SQLite + 真实 Redis + 确定性模拟模型环境验证：运行中显示增量，取消后显示终态，刷新页面恢复历史并重新订阅正在运行的任务。测试没有修改已有广告数据。

真实模型单次实测保存在 [hosting-model-smoke.json](hosting-model-smoke.json)：免费模型 `nex-agi/nex-n2.5-mini:free`，合成广告输入，执行约 4.82 秒、首增量约 1.77 秒、2 次工具调用。模型阶段约 4.80 秒，是本次主要耗时。它没有测试真实业务写入，也不是 P95。

查询压力脚本为 `scripts/benchmark_hosting.py`。必须报告数据库规模与配置；本地少量数据的 HTTP 结果只用于检查链路，不能证明 MySQL 全量容量。生产验收仍须按设计文档完成完整数据集与持续负载测试。

前一轮回归：Python 共 84 项，79 项通过，5 项服务集成用例跳过；Node 5 项通过，前后端 TypeScript 构建通过。2026-09-23 补充执行下述真实 MySQL 验证；本轮托管单元回归 36 项，31 项通过，5 项按环境开关跳过，独立 MySQL 脚本的结果另计。

[本地查询链路记录](hosting-query-smoke.json)：临时 SQLite WAL，仅 1 个用户、1 个 session、3 条历史记录；以 100 请求/秒发送 1,000 次请求，客户端并发上限 50，session 列表 P95 约 3.90 ms、历史 P95 约 4.96 ms，未出现错误。数据量极小、测试约 10 秒，**不能用它声称达成百万 session / 十亿记录性能目标**。


## MySQL 独立进程实测（2026-09-23）

执行 `.venv/bin/python scripts/verify_hosting_mysql.py`，可用 `--mysqld PATH` 指定服务器程序。脚本初始化私有临时数据目录，通过 Unix socket 连接，关闭网络监听；结束后关闭该服务器并删除临时目录，不连接已有业务数据库。

MySQL 8.4.11，6 项验证全部通过，总耗时 30.866 秒，证据见 [hosting-mysql-validation.json](hosting-mysql-validation.json)：

- 8 个独立进程、独立数据库连接同时领取同一 run，仅 1 个成功。
- 同用户 8 个任务，8 个进程竞争，仅 2 个运行，其余排队。
- 10 个用户、20 个任务、20 个进程竞争，全局运行数为 16，每用户不超过 2。
- 8 个进程以相同幂等键同时提交，仅生成 1 个 run 和 1 条用户消息。
- 取消进入 stopping 时继续占用名额；结束后释放，迟到的成功结果不能覆盖 cancelled。
- 实际 Worker 执行确定性测试 pipeline，写入检查点后遭 SIGKILL。另一个 Worker 对象调用真实维护逻辑，约 20.289 秒后将旧任务收敛为 interrupted；旧租约写入被拒绝，恢复检查点创建的新 run 可成功结束。等待真实 20 秒租约到期，没有修改数据库时间模拟过期。

竞争用例直接调用生产 Repository；崩溃用例启动真实 Worker 服务，接管由测试主进程中的 Worker 执行。本次没有验证跨机器网络故障、Docker 子进程清理、真实模型吞吐或大规模持续压力。取消用例验证固定先后顺序，不代表穷尽所有并发交错。
