# 广告业务 MySQL 迁移

广告列表、增删改、点击和操作查单均以 MySQL 8.4 为准。Koa 不再读写 JSON，不保留进程内广告副本；数据库故障返回 503，不回退到旧文件。Agent 的广告快照也从同一数据库读取。

## 初始化和导入

使用 Node 22 或更高版本。准备一个 MySQL 8.4 数据库，账号需要广告表的建表权限和读写权限；所有 Koa 实例连接同一数据库。可以与 Agent 托管共享数据库，广告表使用 `ads_business_` 前缀。

在 `apps/mini-ad-wall/server/.env.local` 配置（该文件已被 Git 忽略）：

```dotenv
ADS_MYSQL_URL=mysql://USER:PASSWORD@127.0.0.1:3306/miniadswall
ADS_OPERATOR_TOKEN=YOUR_OPERATOR_TOKEN
AGENT_SERVICE_TOKEN=YOUR_INTERNAL_SERVICE_TOKEN
ADS_AGENT_API_URL=http://127.0.0.1:8000
AGENT_HOSTING_URL=http://127.0.0.1:8002
```

用户名和密码中的特殊字符必须做 URL 编码。Unix socket 部署还可设置 `ADS_MYSQL_SOCKET=/absolute/path/to/mysql.sock`。不要把 Python 的 `mysql+pymysql://` 地址直接赋给 Koa。

先停止旧文件版本的 Koa，确保最后的点击缓冲已刷盘；保留旧文件备份。然后在 server 目录执行：

```bash
npm ci
npm run build
node --env-file=.env.local dist/scripts/migrate-ads.js --source "$PWD/data.json"
node --env-file=.env.local dist/index.js
```

`--source` 可指向原 `ADS_DATA_FILE` 文件。支持旧广告数组以及 `{ ads, operations }`，保留广告 ID、点击、视频和历史操作返回结果；导入广告的初始版本为 1。历史操作的 before/after 快照进入审计表。

迁移只读取源文件，不删除或修改它。输入先完整校验；广告、历史操作、审计和导入标记在同一事务写入。同一文件重复导入只检查标记，不覆盖后来的业务修改。源文件改变或数据库已有业务记录时拒绝导入。并行迁移使用数据库命名锁串行化；建表可重跑，DDL 不宣称事务回滚。

不提供 `--source` 时只建表，不导入演示广告。应用启动检查表是否可用，不负责自动建表或自动导入。

## 多个 Koa 实例

两个实例配置同一 `ADS_MYSQL_URL`、同一运营身份映射和内部服务配置，以不同端口启动：

```bash
PORT=3001 node --env-file=.env.local dist/index.js
PORT=3002 node --env-file=.env.local dist/index.js
```

前面放负载均衡器即可；广告 CRUD、点击及查单无需粘性会话。上传文件仍使用文件系统，多宿主上传需要共享 `ADS_UPLOAD_DIR` 和统一素材访问地址，不能把每个实例的私有目录当作共享存储；本次未改造分片上传协议或部署负载均衡器。

如果使用 `docker-compose.hosting.yml`，其中 MySQL 默认只在 Compose 内网可访问。宿主上的 Koa 不能直接使用 `mysql:3306`：需要把 Koa 部署到相同网络，或显式配置仅绑定本机的 MySQL 端口转发。不要将数据库端口暴露到公网。

## 接口和一致性

- 原 URL、广告列表数组、创建/删除/点击的成功状态码保持不变。
- 广告返回值增加 `version`。PUT 必须提交最近一次读取的版本；缺失版本返回 400，旧版本返回 409，成功修改后版本加 1。前端编辑表单已接入。点击不修改此版本。
- 出价保存为 `DECIMAL(20,8)`，最多 8 位小数；仍受 `MAX_AD_BID` 限制。
- `(owner, operation_key)` 是操作表主键。相同身份、相同幂等键的并发请求由唯一约束协调；相同指纹重放原结果，不同指纹返回 409。身份采用无尾空格折叠的二进制排序规则。
- 广告变更、操作结果和审计在同一连接、同一事务提交。审计保存该广告的前后值，而不是复制整个列表。发生异常整笔回滚；提交成功但 HTTP 响应丢失时，可从任意实例查单并用原标识重试。
- 修改按广告行加锁并检查版本；不存在全广告列表锁。事务内不调用模型、不做文件上传或其他远端操作。
- 点击使用 `clicks = clicks + 1`，提交后才返回成功，没有 5 秒进程内缓冲。网络重试一次点击仍可能多计一次，本次没有实现点击事件去重、计费或 MQ。
- 排名沿用 `price + price * clicks * 0.42`，数据库生成排名列并建 `(ranking_score DESC, id ASC)` 索引。相同分数按 ID 稳定排序。列表仍返回全部广告；全量查询是否使用索引由优化器决定，本次执行计划检查的是有界排名查询，不宣称完成大数据量分页或性能验收。
- MySQL 连接池每实例最多 10 个连接，等待队列最多 50 个；多实例需合并计算数据库连接预算。共享持久化不等于无限水平扩展。

## 验证

```bash
npm test --prefix apps/mini-ad-wall/server
npm run build --prefix apps/mini-ad-wall/client
```

测试启动独立、可丢弃的真实 MySQL 8.4，通过 Unix socket 连接，关闭 TCP 监听；不连接已有业务数据库。其他安装路径通过 `ADS_TEST_MYSQLD` 指定，没有 MySQL 时明确跳过集成用例。

覆盖旧格式导入、源文件不变、重复迁移、已有数据拒绝合并、事务中途失败、审计写入失败、两个独立 Koa 进程下重复提交和版本冲突、运营身份隔离、40 次并发点击、SIGKILL 后持久化恢复，以及 Agent 使用数据库可信快照。排名索引用执行计划检查。

这些是本地正确性验证，不代表生产部署、跨机器故障恢复或高并发容量结论。
