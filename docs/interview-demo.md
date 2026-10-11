# 面试投屏演示

在 Finder 中双击项目根目录的 `演示.command`，或在项目目录执行：

```bash
./demo.sh
```

成功后自动打开 http://127.0.0.1:5173。后台进程在终端关闭后继续运行。浏览器弹出运营凭据输入框时，输入 `apps/mini-ad-wall/server/.env.local` 中的 `ADS_OPERATOR_TOKEN`；不要把凭据投屏展示。

```bash
./demo.sh status          # 检查网页、Koa、Agent API、托管 API、Worker
./demo.sh stop            # 只停止启动器自己创建的进程
./demo.sh start --no-open # 启动但不打开浏览器
```

## 首次使用条件

- 项目 `.venv` 已安装 `requirements.txt`；前端、server 的 `node_modules` 已安装。
- Node 22 或以上；Mac 优先使用 `/opt/homebrew/opt/node@22/bin/node`。
- 已配置并启动 MySQL；启动器复用现有数据库，不创建账号或覆盖广告数据。
- 根目录 `.env` 包含 `DATABASE_URL`、`REDIS_URL`、`HOSTING_CURSOR_SECRET`、`AGENT_SERVICE_TOKEN`、`OPENROUTER_API_KEY`。当前 Agent 启动脚本只接受免费 OpenRouter 模型。
- `server/.env.local` 包含 `ADS_MYSQL_URL`、运营凭据，以及和根目录相同的 `AGENT_SERVICE_TOKEN`。
- Redis 已运行，或者本机已安装 `redis-server`。本地 Redis 不可连接且对应端口空闲时，启动器会按 `REDIS_URL` 的端口和密码启动 Redis，只监听本机；远程 Redis 由用户自行维护。

启动器优先复用健康服务；缺少 Koa 时先构建并执行广告结构迁移。缺少托管 API 时先执行托管结构迁移。迁移是数据库变更；建议演示使用独立数据库。不会自动导入旧广告或生成演示数据。空库需要按 `docs/ads-mysql.md` 先导入广告。

缺少服务时使用本机端口：网页 5173、Koa 3001、Agent 8000、托管 API 8002。端口已占用而健康检查失败时停止启动流程，不杀原进程。启动失败会关闭本次新建的进程，保留原来运行的服务。

日志与启动记录位于 `logs/demo/`，不纳入 Git。启动器创建的 Redis 使用 `logs/demo/redis-data/` 持久化，停止不会删数据。日志可能包含服务内部信息，勿直接投屏。

## 演示范围

默认演示广告管理、上传、创意生成和 Agent 聊天。Worker 检查仅证明进程存活；不自动调用模型，避免启动时产生请求或费用。开启网页后手动完成一次聊天，确认模型实际可用。

启动器不管理独立召回服务、营销聚合 Worker 和 shell 执行器。营销入口默认关闭；若启用营销写入，启动器执行其结构迁移，聚合 Worker 仍需按 `docs/marketing-aggregation.md` 单独配置专用账号和启动。

面试前准备广告和素材，完整走一遍“查看广告 → 修改出价 → Agent 分析”，保留一段录屏。网页和广告操作在本机运行，模型回答仍需联网。
