# 广告业务与 Agent 的执行边界

本次实现：Koa 保存广告数据并执行运营后台提交的增删改；FastAPI 负责创意、策略生成和助手分析。Python 不修改广告存储。助手尚未接入真实修改工具，不能把模型的 execute 状态当作广告变更成功；已识别的修改请求即使通过确认，也返回 business_execution_unavailable，提示到运营后台操作。

## 配置与启动

- Koa 设置 ADS_OPERATOR_TOKEN（运营凭据）、AGENT_SERVICE_TOKEN（内部服务凭据）、ADS_AGENT_API_URL、MAX_AD_BID（默认 100）。两种凭据使用不同随机值。空运营凭据会拒绝运营请求。
- FastAPI 设置相同的 AGENT_SERVICE_TOKEN，以及 OPENROUTER_API_KEY、OPENROUTER_MODEL、OPENROUTER_API_URL。Anthropic 配置继续用于助手。
- Koa 不会自动加载根目录 .env；通过进程管理器或 shell 导出上述变量。生产环境用 HTTPS，并把 FastAPI 放在内部网络。
- 浏览器首次进行运营操作时输入运营凭据，仅保存在页面内存中，刷新后重新输入。内部服务凭据不传给浏览器。
- 这是单运营账户模型，不支持多租户、细分角色或广告主级归属权限。浏览器提交的 userId、ads 不作为可信身份或分析数据；Koa 使用认证身份 operator 和业务数据快照。

## 请求与变更

- 前端 -> Koa /api/ai/creative、/strategy -> FastAPI /generate/creative、/strategy -> 模型。提示词和模型密钥只在 Python 服务。
- Koa 到 FastAPI：健康检查超时 3 秒，其余调用 55 秒；Python 模型请求超时 45 秒。X-Request-ID 随调用传递并记录日志。
- 运营增删改必须携带运营凭据和 Idempotency-Key。同一标识、同一请求返回第一次结果；同一标识换参数返回 409。
- GET /api/operations/:id 查询结果。前端收到超时或 5xx 后先查询；未确认结果时保留原操作标识，用户重试仍先查询，只有明确 404 才用原标识重发。查询不可用时不重发。刷新后可以恢复相同请求的操作标识。
- Koa 校验必填字段、HTTP(S) 链接、有效正数出价和 MAX_AD_BID。预算字段直接拒绝：当前无预算账户、扣费或余额模型，出价上限不能代替预算控制。
- 广告展示和点击接口不依赖 FastAPI；助手故障时只做本地只读诊断，修改请求拒绝。

## 存储与边界

配置 ADS_DATA_FILE 为固定绝对路径，源码和编译运行使用同一个文件。旧广告数组可直接读取；第一次成功保存时升级为 { ads, operations }。每个操作记录请求指纹、返回结果、修改前后快照与时间。广告变更和操作结果通过同一个临时文件 fsync 后原子替换；写入失败回滚内存并返回错误，不能报成功。

此实现仅支持单个 Koa 进程，操作历史未自动清理。多副本和长期大数据量部署前需迁移到数据库事务和唯一约束。点击仍沿用既有 5 秒缓冲，进程崩溃可能丢失尚未刷盘的点击；当前不包含曝光采集、计费、自动预算优化或真实 Agent 写入工具。

## 验证

- `npm test --prefix apps/mini-ad-wall/server`：HTTP 鉴权、越界出价、重复提交、重启重放、操作审计、可信快照、Agent 故障、前端超时查单及同标识重试。
- `python -m unittest discover -s tests -q`：决策回归、服务认证、生成结果格式、模型超时、禁止虚报变更完成。
- `npm run build --prefix apps/mini-ad-wall/client`：前端类型检查和生产构建。

模型调用测试使用 mock，不代表真实模型凭据或线上链路已验证。
