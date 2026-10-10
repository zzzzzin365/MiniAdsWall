# MiniAdsWall 后端可靠性与扩容修复 Coding Plan

版本：0.1；日期：2026-10-10；状态：CP01、CP02 已完成；其余任务 pending。  
依据：[后端可靠性与扩容修复 SDD](backend-reliability-sdd.md)。本文是实施计划；CP01、CP02 的实际结果见 [阶段验收记录](backend-reliability-acceptance.md)。

## 1. 执行边界

保留 Koa 业务后端、Python 托管 API/Worker、React 前端，修复 SDD 的 G01–G07。广告业务已经使用 MySQL，本计划补齐查询、素材和部署能力，不重复迁移广告 CRUD。

每个任务交付：代码、必要测试、执行结果、发布限制。未运行的用例写 pending；环境不满足写 skipped，不能写 passed。既有工作区改动保留，不做无关重构。

本文路径均相对仓库根目录。标记“新增”的文件和命令接口是计划交付物，实施前不保证存在。新增迁移编号在执行时检查冲突；不改写已经执行过的迁移。

## 2. 任务顺序

| 阶段 | 任务 | 进入下一阶段的条件 |
| --- | --- | --- |
| P0 基线 | CP01–CP03 | 当前缺口复现；增量迁移、协议开关和回归环境可用 |
| P1 广告上下文 | CP04–CP07 | AC01–AC03 通过；新旧输入均可读，页面不再拉全量广告 |
| P2 共享存储 | CP08–CP11 | AC07–AC08 通过；历史素材核对完成，多节点无本地回退 |
| P3 锁与 SSE | CP12–CP16 | AC04–AC06、AC13 通过；完成两步锁升级 |
| P4 执行器 | CP17–CP19 | AC09–AC12 通过；未知工具结果不重放，回收按原宿主确认 |
| P5 部署与恢复 | CP20–CP24 | AC14–AC16、AC18–AC20 通过；真实双节点与兼容回滚可用 |
| P6 容量验收 | CP25–CP28 | AC17 通过；汇总全部 AC，失败和跳过项仍保留 |

依赖编号表示代码实施顺序；阶段门表示发布和验收顺序。每个任务下的“验收”是该任务承担的用例部分，完整 AC 还需第 11 节汇总验证。阶段门未通过时可以准备后续代码，不能启用依赖该能力的生产开关。

## 3. P0：固定基线和升级机制

### CP01：记录现状、复现数量边界

- 状态：done（2026-10-10）；依赖：无。阶段范围通过，正式 AC 仍按验收记录标记。
- 修改：`tests/test_hosting_api.py`、`apps/mini-ad-wall/server/tests/hosting.test.cjs`；新增 `docs/backend-reliability-acceptance.md`。
- 实现：记录 Git revision、既有未提交差异、数据库版本、服务配置及历史验收边界；复现当前 1,000 条可接受、1,001 条被拒绝的输入限制，同时验证 Koa 确实提交全量广告。
- 验证：重跑相关业务 MySQL、托管 MySQL 竞争和客户端恢复回归，记录真实结果；不把历史记录当成此次通过。当前缺口复现与修复后的验收分栏记录。
- 完成标准：AC01 有可重复的失败基线；既有配额、幂等、审批、旧 Worker 拒写的回归结果明确。

### CP02：增加增量迁移、协议版本和功能开关

- 状态：done（2026-10-10）；依赖：CP01。阶段范围通过，正式 AC 仍按验收记录标记。
- 修改：`hosting/migrate.py`、`hosting/schema.py`、`hosting/config.py`、`apps/mini-ad-wall/server/config/index.ts`、`.env.example`；新增迁移记录与执行模块、Koa 增量迁移入口和迁移测试。
- 实现：托管库和业务库分别记录迁移版本、校验值、执行批次；显式迁移命令串行执行 DDL，支持状态查询和重复检查。现有 `metadata.create_all()` 只负责初始化，不能用它替代已有表的升级。
- 实现：增加输入协议版本、执行器协议版本及 `development/multi_node` 模式；分别控制新版上下文写入、共享素材写入、缩小锁范围、SSE 共享读取和动态执行器分配。新功能默认关闭。
- 边界：先加可空字段/新表及兼容读取，再启用新写入；未知协议拒绝领取或明确报错，不按旧格式猜测。已有任务含义不变；应用启动不抢着迁移数据库。
- 验收：AC18、AC19；已有库升级、新库初始化、重复执行、迁移中断、校验值不符都可定位；记录每阶段允许回滚的最低兼容版本。

### CP03：补齐隔离测试和基础观测

- 状态：pending；依赖：CP02。
- 修改：`tests/test_hosting_mysql.py`、`tests/test_hosting_docker.py`、`scripts/verify_hosting_mysql.py`、Koa 测试辅助代码；新增可靠性测试公共辅助模块和事务计时钩子。
- 实现：隔离数据库、Redis 命名空间、对象前缀；提供可控模型、对象延迟、通知丢失、提交响应丢失等注入点。计时覆盖事务、锁等待、领取扫描和连接池等待，供后续前后对比。
- 边界：注入能力仅在测试配置可用；真实 Docker/MySQL/双宿主用例不能退化成 mock 后仍宣称通过；测试数据不写入业务库。
- 验收：为 AC04–AC06、AC09–AC17、AC20 提供可重复运行条件；依赖缺失时测试明确 skipped。

## 4. P1：有界广告上下文与页面查询

### CP04：实现授权范围内的聚合与有限明细

- 状态：pending；依赖：CP02、CP03。
- 修改：`apps/mini-ad-wall/server/models/ads.model.ts`、`services/ads.service.ts`、`services/ad-value.ts`、`types/index.ts`；新增广告上下文服务和对应 MySQL 用例。
- 实现：同一短只读快照事务内查询全量统计和有限明细；权限条件来自现有业务身份边界，不能把客户端筛选当成授权。聚合字段按 SDD 5.1 返回，明细默认 50、最多 100；显式 ID 同样有数量和权限限制。
- 实现：输出 `ad_context.version`、捕获时间、授权范围、聚合、明细和 selection；按排名和 ID 稳定排序，标注截断及省略字段。事务结束后序列化，必要时再删减明细，上下文不超过 64 KiB。
- 边界：过滤长文本和无关内容，不能删掉统计再假装全量；超限和未授权 ID 明确报错。检查索引及 EXPLAIN，不能把全表读入 Node 后再统计。
- 验收：AC01、AC02；0/1,000/1,001/100,000 条及长字段下，统计准确、快照一致、输出有界。

### CP05：先部署新版输入读取能力

- 状态：pending；依赖：CP04。
- 修改：`hosting/api.py`、`hosting/repository.py`、`hosting/pipeline.py`、`mcp/ads_tools.py`；新增输入适配模块及契约测试。
- 实现：读取旧 `ads` 输入并标注 `legacy_snapshot`，读取新版 `ad_context`；不改写历史对象。统计工具消费全量 summary，分析工具明确只使用有限明细；超出样本的查询只能经过有界、鉴权的服务端查询路径。
- 实现：幂等指纹包含用户意图、筛选、session、工具参数和 resume 来源，排除服务端捕获时间及广告快照；并发首次创建只保留胜出 run 的输入，重试返回首次结果。不同意图复用键返回 409。
- 边界：resume 使用原始输入，不重新拼接当前广告事实；历史幂等记录按其原协议和算法判断，不重算后覆盖。输入版本不支持的 Worker 不领取。整个请求最多 256 KiB，消息按 UTF-8 字节校验，不能仅用字符长度代替字节限制。
- 验收：AC01–AC03、AC19；先发布读取兼容版本，保持新版写入开关关闭。

### CP06：切换 Koa 请求生产者

- 状态：pending；依赖：CP04、CP05。
- 修改：`apps/mini-ad-wall/server/routes/agent.routes.ts`、`services/adsAgent.service.ts`、`services/agentHttp.ts`、`middlewares/businessBoundary.ts`、`tests/hosting.test.cjs`、`tests/business-boundary.test.cjs`。
- 实现：浏览器只提交消息、筛选、有限广告 ID 和工具意图；Koa 丢弃客户端 `ads/ad_context`，由 CP04 生成事实，使用已验证身份转发。入口和代理均限制请求字节，错误状态原样按契约传递。
- 发布：确认所有可能接管任务的 Worker 已支持新版、旧 run 排空后启用新写入；对版本不兼容的实例阻止路由/领取，不依赖口头确认。
- 验收：AC01–AC03、AC19；广告变化后的并发重试不创建第二个 run，也不覆盖首次快照；客户端伪造事实和身份无效。

### CP07：广告分页及所有页面消费者迁移

- 状态：pending；依赖：CP04、CP06。
- 修改：Koa `controllers/ads.controller.ts`、`routes/ads.routes.ts`、`models/ads.model.ts`；前端 `apps/mini-ad-wall/client/src/api.ts`、`types/index.ts`、`App.tsx`、`components/VirtualAdGrid.tsx`、`components/DataDashboard.tsx` 及其他实际全量列表消费者。
- 实现：新增默认 50、最大 100 条分页契约；游标签名并绑定筛选、排名值和 ID。页面按页加载、按 ID 去重；看板使用全量聚合，不用已加载页计算总数和均价。
- 边界：排名变动允许跨页结果变化，不承诺静态快照；刷新重置游标。旧数组接口保留兼容窗口，查清全部仓库内调用方迁移后才停用。
- 验收：AC01、AC02、AC17 的查询准备；空列表、权限错误、过滤无匹配、排名变化和连续翻页可区分；构建和实际页面交互通过。

## 5. P2：共享对象与上传

### CP08：统一对象配置和资产元数据

- 状态：pending；依赖：CP02、CP07。
- 修改：`hosting/objects.py`、`hosting/config.py`、Koa `config/index.ts`、`types/index.ts`、业务增量迁移；新增 Koa 对象存储客户端与资产仓储。
- 实现：资产记录 owner、对象键、大小、类型、校验值、状态和时间；广告仅引用 ready 资产。托管对象增加持久生命周期记录，供引用发布和回收共同校验。公共素材 URL 由配置域名生成，私有 workspace/工具输出沿用鉴权；稳定资产 ID 不含节点地址。
- 实现：multi_node 要求共享存储配置且探测失败则不就绪；本地目录只供 development。对象键不可变，重试不覆盖已引用内容；存储失败不能回退本地目录。
- 验收：AC07、AC18；A 写 B 读，包括托管输入、检查点、workspace 和工具输出；未授权读取和错误配置明确失败。

### CP09：实现直传分片协议和完整性验证

- 状态：pending；依赖：CP08。
- 修改：Koa `routes/index.ts`、`services/upload.service.ts`、业务增量迁移；新增上传 routes/controller、会话仓储和清理入口。
- 实现：按 SDD 7.2 增加初始化、parts、complete、abort 接口及鉴权查单接口；以短事务保护单用户最多 2 个未完成上传。签名绑定对象键、分片号和有效期，文件上限 500 MiB、常规分片 5 MiB，适配所选存储服务实际约束。
- 实现：complete 在事务外核对存储端分片、总字节和完整性，再幂等写 ready；相同参数重试返回首次结果，不同内容复用键返回 409。响应丢失后可查单收敛。
- 边界：multipart ETag 不能冒充整文件哈希；无完整性接口时保持 verifying，通过有界流校验后发布。abort/complete 竞争不得发布已取消会话或泄漏分片；24 小时过期中止。
- 验收：AC08；缺片、越权签名、乱序重试、签名过期、大小不符、完成结果未知均不产生可引用半成品。

### CP10：前端直传和旧上传流式适配

- 状态：pending；依赖：CP09。
- 修改：前端 `utils/chunkedUpload.ts`、`workers/hashWorker.ts`、`api.ts`、`components/AdModal.tsx`；Koa `services/chunk-upload.service.ts`、`controllers/chunk-upload.controller.ts`、`routes/chunk-upload.routes.ts` 和实际旧上传入口。
- 实现：前端最多并发 4 个分片，分块计算校验值、更新进度、有限重试、取消和断线查单；不一次性读完整文件。仅 ready 返回值可提交广告表单。
- 实现：旧接口通过有界流式管道接入共享存储，等待背压，处理请求断开、大小和超时；无法兼容时返回明确升级错误。生产不保留“先本机落盘后上传”的中间路径。
- 验收：AC08、AC17 的上传准备；断开后缓冲和请求可回收；实际浏览器上传、取消、重试通过，Node RSS 不按整文件大小增长。

### CP11：历史素材迁移和孤立对象回收

- 状态：pending；依赖：CP08–CP10。
- 新增：`apps/mini-ad-wall/server/scripts/migrate-assets.ts`、资产迁移记录、对象回收入口及测试；修改 `hosting/cleanup.py` 和对象引用查询。
- 实现：生成本地路径→对象键→业务引用映射，上传后核对长度和哈希，再短事务更新引用；重复执行不重复发布。不改写用户外部 URL，保留本地源文件和失败项。
- 实现：冻结旧本地上传入口，处理在途分片后切换；回收未完成上传和孤立对象。内容寻址对象至少等待 24 小时，做两次引用检查；引用发布与回收均锁同一对象元数据行。回收短事务核对无引用并标记 deleting，锁外删除，再记录 deleted；发布不得引用 deleting/deleted 对象，须等待或重新写入验证后发布。回收只锁对象行，不反向锁 run/session。
- 验收：AC07、AC08、AC18；停止 A 后 B 仍能读；迁移数量、哈希和引用一致；重复迁移、并发引用、失败恢复及回滚副本核对通过。

## 6. P3：锁、对象提交和 SSE

### CP12：统一实体锁，暂时保留全局锁

- 状态：pending；依赖：CP03、CP11。
- 修改：`hosting/repository.py`、`hosting/worker.py`、`hosting/cleanup.py`、`hosting/schema.py`、增量迁移和相关测试。
- 实现：逐个登记所有状态写入路径，包括 API、Worker、审批、维护和执行器；锁序统一为容量→用户容量（需要时）→session→run→tool/approval，同类多行按 ID 排序。第一版仍保留原全局锁。
- 实现：事件序号在 run 锁内分配并插入；会话消息序号在 session 锁内分配；心跳使用状态、fence、未过期租约条件更新，影响行数不是 1 即失去资格。租约和 deadline 使用数据库服务端时间。
- 验收：AC04、AC06；竞争写入不重复序号、无反向补锁，审批/取消/恢复仍守住所有配额。记录全部写入者的升级版本。

### CP13：外部 I/O 移出事务，短事务提交引用

- 状态：pending；依赖：CP08、CP12。
- 修改：`hosting/repository.py`、`hosting/worker.py`、`hosting/objects.py`、`hosting/pipeline.py`、必要 API 调用及测试。
- 实现：逐个拆分 create_run、checkpoint、workspace 发布、工具结果保存及实际含对象 I/O 的路径：锁外读取/写不可变对象→短事务重验权限、版本、状态、fence、租约、deadline→提交引用与事件→提交后通知。
- 实现：心跳续约线程/协程不被对象延迟堵住；版本冲突返回 409；事务失败的对象交给 CP11 回收，不能立即删掉可能共享的哈希对象。
- 边界：仅无外部副作用的短事务可死锁重试，最多 3 次并随机退避；提交结果未知按幂等键或实体查单，不重跑模型或 shell。事务内只保留数据库操作。
- 验收：AC03、AC05、AC06、AC18；注入 5 秒对象延迟不持全局锁、不造成无关事件停顿；过期 Worker 无法发布刚上传的结果。

### CP14：启用普通事务与容量事务分离

- 状态：pending；依赖：CP12、CP13。
- 修改：`hosting/repository.py`、`hosting/config.py`、领取相关索引/迁移、`tests/test_hosting_mysql.py`、`scripts/verify_hosting_mysql.py`。
- 实现：确认全部写入者支持 CP12 后，启用独立普通事务；全局容量锁仅保护创建/恢复、领取、审批恢复、waiting_approval 和终态等容量转换。普通事件、心跳和查询不获取全局锁。
- 实现：容量仍由受保护的任务状态查询，保持全局 16、用户 2、排队全局 1,000/用户 10及单 session 非终态唯一；stopping 占用运行容量。领取扫描默认起始 100，保持用户轮转，记录跳过原因和最老等待时间。
- 验收：AC04–AC06；重跑现有 MySQL 六项回归和跨节点交错状态测试，证明缩锁后无超卖。旧写入者存在时拒绝切换新锁模式。

### CP15：按 run 共享 SSE 读取和慢连接隔离

- 状态：pending；依赖：CP13、CP14。
- 修改：`hosting/api.py`、`hosting/auth.py`、事件提交路径；新增 `hosting/event_hub.py` 及共享读取测试。
- 实现：事件持久提交后发送 Redis run/seq 通知；每实例每活跃 run 一个共享读取器，合并重复通知，2 秒兜底补读。历史补发按单连接游标有界读取，缓存有限，最后一个订阅离开后释放读取器。
- 实现：连接缓冲最多 256 KiB，按字节计数，慢连接关闭并通过游标重连，不阻塞其他连接；15 秒心跳重查权限，关闭及时释放连接票据。单 API 最多 500 连接、单用户最多 5，超限 429 + Retry-After。
- 边界：通知丢失/重复/乱序不影响持久事件，通知失败不回滚已提交事务；Redis 通知通道故障仍可 SQL 补读，认证或准入依赖故障按 SDD 拒绝相关新请求。
- 验收：AC13、AC15、AC17；查询次数主要随活跃 run 数增长；100 连接、重复通知、断线、慢客户端及权限失效均可观察。

### CP16：前端事件去重与刷新恢复

- 状态：pending；依赖：CP15。
- 修改：前端 `api.ts`、`components/AIAssistantPanel.tsx`、必要类型；Koa `tests/client-recovery.test.cjs` 及浏览器恢复验证脚本。
- 实现：按 run 保存已处理 event_seq，重复序号不重复拼接 delta；重连携带游标；刷新与切换 session 从持久历史/任务状态恢复，避免历史文本和重放 delta 重复叠加。
- 实现：410 恢复持久历史并提示进度事件过期；429 按 Retry-After 退避；SSE 断开、审批待处理和 stopping 明确呈现。
- 验收：AC13、AC14；真实浏览器刷新、重复事件、服务滚动退出后文本不重复、审批不丢、状态不误报完成。

## 7. P4：多宿主执行器

### CP17：持久注册、执行归属与容量预留

- 状态：pending；依赖：CP02、CP14。
- 修改：`hosting/schema.py`、`hosting/repository.py`、`hosting/config.py`、增量迁移；新增执行器注册/查询模块及竞争测试。
- 实现：新增 executor_nodes、tool_executions 和 tool_calls 归属字段；executor_id 标识宿主，epoch 标识本次启动。endpoint 仅来自受信任配置，租约以数据库时间计算。
- 实现：生成唯一 execution_id 和确定性 sandbox_id；在短事务内选择健康节点、锁节点容量并保存归属。未确认清理的执行仍占名额；无容量等待有界且计入 run deadline。
- 边界：分配事务遵守 CP12 锁序；新协议字段未完整写入时不能发起执行；旧固定地址协议只在兼容窗口可用，不与新版归属混用。
- 验收：AC09、AC10、AC12；多个 Worker 竞争不超预留容量，任何执行开始前都能查到唯一持久归属。

### CP18：幂等执行、查单与结果提交

- 状态：pending；依赖：CP13、CP17。
- 修改：`hosting/sandbox.py`、`hosting/executor.py`、`hosting/worker.py`、`hosting/sandbox_entry.py` 及协议测试。
- 实现：执行请求绑定 execution_id、tool_call_id、run fence、参数哈希和归属；执行器再次核对状态/租约，按确定性容器名和持久执行状态防重复启动。启动前恢复/核验已有容器，不能只靠内存字典去重。
- 实现：提供 execution_id 查单；启动响应丢失、重复请求和执行器重启均查询原执行，不换宿主重跑。输出和 workspace 上传在锁外，提交结果继续校验执行代次；工具容量仅在清理确认后释放。
- 边界：保留现有无网络、非 root、只读根、CPU/内存/PID/目录限制；限制输出缓冲。发现同 execution_id 对应多个容器应告警并阻止继续执行。
- 验收：AC09、AC11；真实 Docker 下，启动前后丢响应及重复 RPC 均只有一个有效容器；参数变化拒绝，旧 Worker 结果拒写。

### CP19：原宿主回收、独立 watchdog 和分区保守处理

- 状态：pending；依赖：CP18。
- 修改：`hosting/executor.py`、`hosting/sandbox.py`、`hosting/worker.py`、`hosting/cleanup.py`、Docker 故障测试。
- 实现：取消先撤销旧写入资格，再按持久归属请求清理；进程组 TERM→宽限 3 秒→KILL→等待退出→移除容器。回复绑定 execution_id、executor_id、epoch、宿主不存在证明和时间。
- 实现：watchdog 独立于 Worker；执行器启动先扫描本宿主旧 epoch/过期/孤立容器再接收任务。错误宿主的 not_found 不算成功；失联保留 stopping 和容量并告警。
- 边界：释放必须有原宿主清理确认或受信任基础设施隔离证据；数据库 fence 不能代替进程停止证明。shell/test 未知结果不重放，只读任务按既有检查点显式 resume。
- 验收：AC10–AC12；跨宿主接管、执行器重启、孙进程忽略 TERM、洪泛、OOM、网络分区均检查实际容器和持久记录。

## 8. P5：依赖恢复、部署与发布

### CP20：Redis 故障与模型门协调恢复

- 状态：pending；依赖：CP02、CP14、CP19。
- 修改：`core/model_gate.py`、`hosting/config.py`、`hosting/auth.py`、恢复状态增量迁移、`tests/test_model_governance.py`；新增真实 Redis 切换测试。
- 实现：明确可绕过的缓存与不可绕过的认证、限流、模型准入；检测模型门状态缺失/代次不一致，暂停新准入。持久记录统一恢复代次，短事务选定协调者，所有 Worker 服从同一恢复状态。
- 实现：确认旧调用结束，或等待覆盖 execution + grace 的上限后重建共享门；旧代次不得继续发新调用。恢复失败仍拒绝准入，各 Worker 不能各自重建或退回本地门。
- 边界：Redis 拓扑需隔离旧主，所有模型调用包括预检共用配置和命名空间；已有 run 容量与模型调用容量分别统计，不合并。
- 验收：AC15；断开、清空状态、重启和主备切换期间，真实多 Worker 不突破模型门上限，历史仍可读，恢复等待和代次有证据。

### CP21：探针、退出排空与连接池预算

- 状态：pending；依赖：CP19、CP20。
- 修改：Koa `app.ts`、`index.ts`、`services/ads.database.ts`；`hosting/api.py`、`hosting/worker.py`、`hosting/executor.py`、`hosting/config.py`。
- 实现：区分 liveness 与 readiness，依赖故障摘除入口而不是反复重启；多节点对象存储故障不回退本地。MySQL 切换时暂停新写入/新工具，无法确认提交则查单。
- 实现：默认 30 秒退出宽限，API 停新请求并提示 SSE 重连，Worker 停领取；在途任务完成或持久进入 interrupted/stopping，回收超时不伪报成功。
- 实现：按最大副本数汇总 Koa/API/Worker/执行器/维护连接池，保留至少 20% 运维余量，超预算启动配置拒绝或部署校验失败。
- 验收：AC14–AC16；滚动退出无丢失确认结果，MySQL/Redis/S3 故障策略与 SDD 一致。

### CP22：真实多节点模板和依赖高可用配置

- 状态：pending；依赖：CP08、CP19–CP21。
- 新增：`deploy/multi-node/` 的服务模板、配置示例、启动校验与运维说明；修改 `docker-compose.hosting.yml`、`config/nginx/nginx.conf`、相关 Dockerfile 和 `.env.example`。
- 实现：两个独立 Linux 应用节点，Koa/API/Worker 均有副本，工具宿主各有可信执行器；入口自身可切换，托管 API 不固定代理到单实例；私有执行器端点不向浏览器暴露。
- 实现：写明采用的 MySQL 自动切换和旧主隔离方案、Redis 切换连接方式、共享对象服务、镜像版本、权限、资源和连接预算；实际提供商未确定时保留待配置项，不伪造现成 HA 集群。
- 边界：Compose 仅为本地集成；两个同机容器不能计作两宿主。RPO=0 必须有已确认事务的持久化/复制保证和故障证据，不能仅凭异步主备存在就声明。
- 验收：AC07、AC10、AC14–AC16；记录不同宿主身份，实际切断节点和旧主，确认路由摘除、SSE 恢复及持久状态。

### CP23：完整监控、关联日志和告警

- 状态：pending；依赖：CP03、CP15、CP19、CP22。
- 修改：`config/prometheus.yml`、各服务观测代码；新增告警规则与面板定义。
- 实现：覆盖 Koa、托管 API、Worker、执行器和对象上传；采集 SDD 12 节的接口、数据库池/锁、领取、队列年龄、SSE、模型门、租约、OOM、回收和孤立对象指标。
- 实现：request/run/execution ID 用于日志与追踪关联，不作为 Prometheus 高基数标签；脱敏 token、用户输入和签名 URL。按 SDD 配置错误率、锁等待、队列年龄及遗留执行告警。
- 验收：AC20；主动注入故障，核对指标变化和告警实际触发；能够定位失败阶段，新增数据量不导致标签数无界增长。

### CP24：发布预检、兼容回滚和备份恢复

- 状态：pending；依赖：CP02、CP06、CP11、CP14、CP19、CP22、CP23。
- 新增：发布预检/备份核对脚本、`deploy/multi-node/` 发布与恢复手册；修改相关实现说明。
- 实现：按 SDD 11 节执行“迁移→兼容读取→服务升级→排空旧 run→新协议写入→资产切换→锁模式/SSE→多节点”；每一步记录版本、开关、记录数、对象映射、失败项和可回滚版本。
- 实现：预检拒绝未知输入 Worker、新旧锁模式混用和工具归属协议降级；回滚只切兼容版本，不删除新增业务/审计/任务数据，也不把共享键解释成本地文件。
- 实现：在隔离环境恢复数据库和对象备份，验证广告/审计/历史及全部引用；中断迁移和中断发布后重复执行可收敛，DDL 失败用明确修复步骤处理。
- 验收：AC18、AC19；实际恢复和兼容回滚通过；仅写手册不算完成。

## 9. P6：数据、容量和故障证据

### CP25：生成正式验收数据

- 状态：pending；依赖：CP07、CP11、CP24。
- 新增：`scripts/seed_reliability_dataset.py` 和数据清单。
- 实现：隔离环境生成 100,000 广告、100,000 session、1,000,000 历史记录，包含 10/1,000/100,000 记录的长 session 和实际对象；生成可校验预期聚合与授权分布，数据 seed 固定。
- 边界：脚本默认拒绝非测试数据库/对象前缀；重复运行按数据集标识收敛，清理只操作自己生成的记录。不能只增加无关联空行。
- 验收：AC01、AC07、AC17 的数据前提；保存表行数、对象清单、索引、EXPLAIN 和生成参数。

### CP26：持续负载与混合上传验收

- 状态：pending；依赖：CP23、CP25。
- 新增：`scripts/benchmark_reliability.py`、负载配置和机器可读结果；调整 `scripts/benchmark_hosting.py` 的适用范围说明。
- 实现：按 SDD 13.2 分别测广告页/session 页/history 页各 200 请求/秒、200 客户端并发上限，预热 5 分钟、测量 30 分钟；可控模型约 5 秒、模型门 12、2 run/秒，所有预检调用计入模型并发。
- 实现：100 SSE、至少 20 用户、每用户最多 5 连接，保持 30 分钟并记录实际事件率；两个用户各上传 500 MiB，同步测量查询退化和 RSS。
- 判定：查询 P95 ≤ 200 ms、P99 ≤ 500 ms、非预期错误率 ≤ 0.1%；提交 P95 ≤ 500 ms；通知 P95 ≤ 1 秒、丢通知 ≤ 3 秒补发；上传时查询 P95 增加 ≤ 20%。其余配额与目标直接沿用 SDD，不通过降低压力悄悄改目标。
- 验收：AC13、AC17；保存到达/完成吞吐、客户端排队、429/503/失败、资源和持续趋势；可控模型成绩与真实模型验证分别报告。

### CP27：双宿主故障和未知结果验收

- 状态：pending；依赖：CP19、CP20、CP22–CP26。
- 新增：`scripts/verify_reliability_failures.py`、故障场景配置与结果；扩展真实 Docker/MySQL 回归。
- 实现：逐个执行 AC04–AC16、AC18–AC20 所需的节点故障、Worker SIGKILL、错误宿主清理、执行器重启/分区、通知丢失、Redis 状态丢失/切换、MySQL 切换/提交结果未知、发布中止和备份恢复。
- 判定：入口摘除 ≤ 15 秒、SSE 恢复 ≤ 30 秒；依赖和执行器可达时 Worker 状态收敛 ≤ 60 秒；可达执行器取消 ≤ 10 秒；MySQL 业务恢复目标 ≤ 120 秒。执行器不可达时必须保持 stopping 和告警，不拿提前放名额换速度。
- 边界：故障脚本只作用于显式登记的验收节点与测试资源；保存原配置和恢复动作。每次核对已确认写、幂等结果、配额、旧 fence 拒写和原宿主容器，重启成功不是通过条件。
- 验收：实际不同宿主、真实 Docker/MySQL/Redis 切换有记录；不满足环境的用例 skipped。

### CP28：汇总验收与修正文档

- 状态：pending；依赖：CP01–CP27。
- 修改：`docs/backend-reliability-acceptance.md`、`README.md`、`docs/agent-hosting-implementation.md`、`docs/ads-mysql.md` 及相关部署说明；不改写历史验收 JSON。
- 实现：逐项填 AC01–AC20 的执行环境、命令、预期/实际、证据路径、状态和剩余问题；核对 INV01–INV08，不将测试模拟等同于真实依赖结果。
- 完成标准：所有必需用例在规定环境通过后才能标记完整验收；存在失败或 skipped，只交付阶段结果。分别写清代码已实现、真实多节点已验证、持续负载已验证三种事实。

## 10. 验证命令与报告规则

以下是已有入口，按改动范围运行；环境变量使用隔离测试配置，真实 MySQL/Docker 前提不足时不能算通过：

```bash
npm test --prefix apps/mini-ad-wall/server
npm run build --prefix apps/mini-ad-wall/client
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p test_hosting_mysql.py -v
.venv/bin/python -m unittest discover -s tests -p test_hosting_docker.py -v
.venv/bin/python scripts/verify_hosting_mysql.py
git diff --check
```

托管 MySQL 测试需 `HOSTING_TEST_MYSQL_URL` 指向专用测试库；Docker 测试需 `HOSTING_TEST_DOCKER=1` 和实际工具镜像。`verify_hosting_mysql.py` 自建一次性 MySQL，默认服务器路径未安装时用 `--mysqld` 指定；它只证明本机独立进程回归，不证明跨宿主 HA。

新增三类脚本必须提供 `--help`、隔离配置、结果输出位置、资源清理说明及非零失败退出码。具体参数随实现固定并补入验收文档，本文不提供尚不存在的可运行命令。

每份结果保存：Git revision 和必要差异标识、镜像/依赖版本、CPU/内存/网络、数据库与 Redis 拓扑、对象服务配置摘要、数据集、负载口径、实际值、失败/跳过项、故障时间线、状态/对象/容器核对。记录中不包含凭据。

## 11. AC 覆盖表

| 用例 | 主要任务 | 当前状态 |
| --- | --- | --- |
| AC01 | CP01、CP04–CP07、CP25 | pending |
| AC02 | CP04、CP06、CP07 | pending |
| AC03 | CP05、CP06、CP13 | pending |
| AC04 | CP12、CP14、CP27 | pending |
| AC05 | CP13、CP14、CP27 | pending |
| AC06 | CP12–CP14 | pending |
| AC07 | CP08、CP11、CP22、CP25 | pending |
| AC08 | CP09–CP11 | pending |
| AC09 | CP17、CP18、CP27 | pending |
| AC10 | CP17、CP19、CP22、CP27 | pending |
| AC11 | CP18、CP19、CP27 | pending |
| AC12 | CP17、CP19、CP27 | pending |
| AC13 | CP15、CP16、CP26、CP27 | pending |
| AC14 | CP16、CP21、CP22、CP27 | pending |
| AC15 | CP15、CP20–CP22、CP27 | pending |
| AC16 | CP21、CP22、CP27 | pending |
| AC17 | CP07、CP10、CP15、CP25、CP26 | pending |
| AC18 | CP02、CP08、CP11、CP13、CP24、CP27 | pending |
| AC19 | CP02、CP05、CP06、CP24、CP27 | pending |
| AC20 | CP03、CP23、CP27 | pending |

## 12. 发布时必须守住的条件

1. 新版输入生产者上线前，全部可能接管任务的 Worker 已能读取该版本；历史 resume 仍读取原输入。
2. 全局锁移除前，全部状态写入者已统一实体锁；只升级 Worker 不够。
3. 多节点素材写入前，共享存储和旧素材核对完成；不能静默回退本地。
4. 动态执行器启用前，执行归属、幂等查单和原宿主清理协议同时可用；未知结果不换宿主重跑。
5. Redis 模型门状态重建前，全体 Worker 进入同一恢复代次并完成旧调用等待；不能各自恢复。
6. 完整验收前，AC01–AC20 仍按真实结果记录；实现完成不等于高可用和容量已验证。
