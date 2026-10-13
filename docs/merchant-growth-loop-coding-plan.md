# 个人商家投放闭环 Coding Plan

- 日期：2026-10-13；依据：[SDD v1.1](merchant-growth-loop-sdd.md)。
- MF00–MF15整阶段仍未验收；创意工作台子集已落地，见[实现记录](merchant-creative-studio-implementation.md)。不得将子集交付勾选为完整M1/M2。
- 每项顺序：确认依赖 → 契约/数据 → 服务/Worker → UI → 验收证据 → 更新状态。
- 本计划是旧 CP/AF 的集成入口，不重复实现它们的同义表、执行器或案例库。

## 1. 执行清单

| 编号/状态 | 任务 | 依赖 | 交付与验收 |
|---|---|---|---|
| MF00 pending | 冻结基线与能力矩阵 | 无 | 分支/改动/文件hash、迁移与数据库状态、旧CP05/06及AF实际状态；AC01基线 |
| MF01 pending | 协议、表归属和状态冻结 | MF00 | Schema/正反例、表映射、迁移capability manifest；全部AC可追踪 |
| MF02 pending | 商品档案/资产与授权底座 | MF01 | 增量迁移、事实版本、上传登记、scope鉴权；AC01/02/03 |
| MF03 pending | 媒体分析任务与证据 | MF02 | 有界预处理、OCR/ASR/视觉适配、托管桥接、证据产物；AC02/04 |
| MF04 pending | 场景、创意与审核版本 | MF03 | 冻结输入、claim映射、审核、生成用量钩子；AC03/04 |
| MF05 pending | M1向导与模型验收 | MF04 | 商品→体检→场景→素材四段交互；M1模型评测及AC11相关项 |
| MF06 pending | 试投状态机/分组与预算 | MF05、旧S1验收、CP15–17使用部分 | 实验配置/组预算、稳定分组、实际分发关联；AC05 |
| MF07 pending | 承接页、预约与核销 | MF06 | 页面发布、线索状态、券码、来源适配；AC01/06 |
| MF08 pending | 经营账单与实验报告 | MF07 | report快照扩展、费用分账、固定统计规则；AC07 |
| MF09 pending | 建议决定与下一轮执行 | MF08、AF00–07、相关S3执行底座 | AF卡片、实验提案/人工确认/回执；AC08 |
| MF10 pending | M2端到端验收 | MF09 | 五步向导、真实MySQL/Worker对账和故障演练；AC01–08/11 |
| MF11 pending | 历史经验与偏好复用 | MF10、AF08–12 | scope检索、商品版本关联、撤回/导出；AC09与固定对照评测 |
| MF12 pending | 活动包报价与用量 | MF04、MF10 | 报价版本、reserve/consume/release、退款演示；AC10 |
| MF13 pending | M3留存采集与分阶段发布 | MF11、MF12 | 漏斗/复用指标、隐私清理、开关/回滚；M3浏览器验收 |
| MF14 pending | 公众商家身份与支付 | MF13 | 独立subject、旧入口隔离、真实支付回调/退款；AC01/10/12相关项 |
| MF15 pending | 单一真实媒体适配与M4 | MF14、渠道授权可用 | 实际提交、查单、暂停、费用回收及对账；AC12 |

MF14/MF15 需要实际选定身份/支付/广告服务并核对当时官方文档。凭据、渠道能力或账号资格缺失仅阻塞M4，不阻塞M1–M3。不得用模拟结果勾选M4。

## 2. 各项实施要求

### MF00–MF02：基线、契约与数据

- 保存 `docs/validation/merchant-growth-loop/baseline.md` 与 baseline.json；不清理当前未提交代码，不改写历史评测产物。
- 检查上级及模块 AGENTS.md；使用当前可执行 Node 22；记录 MySQL/Redis/模型依赖是否可用，不输出密钥。
- MF01 新建 `contracts/merchant/v1/schema.json`、examples.json；Koa/Python用同一批fixtures运行正反例。包含越权scope、未知字段、超限、非法证据、浮点金额、过期revision与非法状态迁移。
- MF01 将 SDD 逻辑对象逐一映射到既有或新增物理表，确定019–021候选编号；若旧CP已经实现对应对象，明确ALTER和数据回填方案。冻结JSON字段及DDL后再写业务逻辑。
- MF02 修改营销迁移登记/结构检查器，增加所需版本集合判断；禁止按最大版本推断缺失能力。覆盖空库、013升级、部分旧阶段存在、重复执行、DDL中断恢复、校验冲突。
- 新增 `server/services/merchant/{authorization,product,asset}.service.ts` 和 `routes/merchant.routes.ts`；复用事务连接，不在mutate内部另开事务。
- 验证撤权、跨品牌、猜asset ID、签名URL过期；商品修订追加不覆盖。迁移通过需要真实隔离MySQL。

### MF03–MF05：媒体理解、生成与M1

具体执行按[营销创意工作台 SDD 的CS00–CS07](merchant-creative-studio-sdd.md)拆分。理解/文本/图片/视频能力独立验收；首次投放建议作为草稿纳入M1，不依赖AF或M2报告。按strict-two-model-v1：DeepSeek负责非生成任务；明确视频请求才调用Seedance；图片生成阻断，不自动调用Seedream。

- 新增 `marketing/merchant/{contracts,preprocess,analysis,generation}.py`；适配器接口分离 OCR、ASR、视觉理解和生成。按现有模型客户端扩展能力，不擅自更换整套框架。
- 与 AF 桥接共用 task/outbox/hosted-client；media job 输入固定，succeeded/partial完整output_ref经服务端校验后才落业务结果；SSE只展示进度。
- 预处理限资源/时间；证据时间和bbox可追溯；注入恶意OCR指令、格式损坏、部分模块失败、旧租约回写，验证不会发布错误素材。
- 现有 Seedance 接口套持久化任务/receipt，不改变未知提交不得自动重试的规则。没有供应商查单能力时提供“待核查”操作记录。
- 新增 `client/src/components/merchant/{MerchantWorkspace,ProductEditor,MaterialInspector,ScenarioPicker,CreativeReview}.tsx` 及共用types/API。
- 体检结果可点到图像区域/视频时间点；商品事实由商家确认；审核前展示原稿、改动、缺据。支持人工制作和模型失败恢复。
- MF05运行冻结素材评测，保存逐样本结果、失败、真实模型成本和耗时。仅mock通过时M1模型验收不得勾选。

### MF06–MF08：投放、承接与结果

- 在现有 delivery/serving、预算与营销资源服务上扩展，不新建平行事件账本。实现原CP预算能力后再开放start。
- 新增 experiment.service、assignment、landing、lead、redemption；冻结实验字段与primary metric；运行配置不可改。两进程并发start验证同campaign只有一个运行槽。
- 预算扣减测试同时竞争总/日/组额度、重复点击、旧凭证和暂停；每笔费用能回溯serving decision，账本与余额一致。
- 承接页不泄露商家后台token；公开预约独立限流/幂等；核销必须operator身份。支付事实经来源适配接现有conversions，人工登记明确标识。
- 新增 ExperimentSetup、LandingEditor、LeadInbox、MerchantStatement、ExperimentComparison；先服务端定义口径再做UI。
- 经营账单复用 report.service，单独服务费来源；冻结统计算法fixtures，覆盖0/0、0/100、满转化、小样本、窗口未成熟、污染、退款更正。
- 报告刷新创建新revision，旧hash与提案仍可查；页面展示“数据不足/无法比较”，不能只显示红绿箭头。

### MF09–MF10：优化执行与M2

- 先实现/验收AF基础反馈；本计划增加experiment/scenario/product版本关联，不能仅接聊天字符串。
- 增加create_next_experiment_draft、pause_onsite_experiment的独立动作schema/执行器；新实验start单独确认，不借旧set_bid白名单越权。
- intent落库后Worker执行；超时查单、撤权、版本过期、预算变化、取消、进程崩溃均有确定状态。接受建议不自动创建收费任务。
- 浏览器走完：建商品→素材体检→确认场景/创意→试投→消费者曝光/点击→预约/核销→聚合→报告→建议→修改/确认→下一轮草稿。
- synthetic全链运行必须打标；另记录真实用户点击/模型调用覆盖范围。MF10通过只称站内闭环，不称接通巨量/聚光。

### MF11–MF13：复用、活动包与留存

- 扩展AF案例引用，当前scope/版本/可见性SQL复核；案例查询与撤回竞争测试，索引延迟不造成越权。更新偏好需显式确认。
- 与无案例模式做固定对照，禁止同时更换模型、问题或当前报表；报告事实污染和人工修改量，不只比较回答流畅度。
- 021迁移实现报价/用量账，计量hook接MF04；两Worker完成同任务只消耗一次，未知提交不释放后再次收费。额度不足在提交供应商之前拒绝。
- 增加活动漏斗与再次活动指标定义/排除规则；同时提供资料导出、经验撤回、联系人到期清理任务与状态页。
- 分阶段开关默认关闭；新入口关闭后已接受任务继续核对。发布/回滚演练保留版本兼容和账务历史。

### MF14–MF15：真实商业入口

- 冻结账号/品牌成员/会话/支付与媒体adapter契约；核对全部旧广告、AI、上传、对象、SSE和operations入口的租户隔离。
- 支付回调验签/重放/金额币种核对/退款、渠道提交unknown/暂停失败/凭据撤销/数据延迟必须有真实测试或受控沙箱证据。
- 单一渠道先验收，再复制适配；真实硬预算不支持则明示软阈值和风险余量，不承诺绝不超支。
- 真实测试活动需商家确认具体预算和素材；完成后核对渠道账户费用、平台媒体账、服务账和报告。全部通过才开放对应入口。

## 3. 验证命令与证据纪律

以下是执行阶段命令，不表示本次已运行：

```sh
# Koa 构建/回归；运行前确认使用隔离测试库
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/server test
# 前端类型检查和构建
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/client run build
# 新增后按任务运行针对性 Python 测试
.venv/bin/python -m pytest tests/test_merchant_analysis.py tests/test_merchant_generation.py
# 文档/代码空白检查
git diff --check
```

每项新增测试覆盖状态、业务不变量和故障，不为静态文案写镜像测试。数据库/模型/浏览器缺失时标 skipped/pending，不能将跳过当通过。先跑相关检查，只有依赖变化或回归风险需要时扩大范围。

## 4. 完成定义

- M1：MF00–05及AC01–04、对应AC11证据通过。
- M2：MF06–10及AC05–08、完整AC11通过，并保留M1回归。
- M3：MF11–13及AC09–10通过，固定复用评测、留存采集和回滚已验证。
- M4：MF14–15、AC12及公众身份/真实支付验收通过。
- 任何阶段禁止预写商业提升、生产容量或全渠道接入结论。

## 5. 验收追踪矩阵

| 验收 | 责任任务 | 验证层 |
|---|---|---|
| AC01 | MF02、MF07、MF14 | HTTP/真实MySQL/对象与SSE |
| AC02 | MF02、MF03 | 文件处理/真实媒体/资源限额 |
| AC03 | MF04、MF05 | 契约/模型冻结集/审核界面 |
| AC04 | MF03、MF04、MF09 | Worker/供应商故障注入/查单 |
| AC05 | MF06 | 多进程MySQL/分发/预算账本 |
| AC06 | MF07 | 公开接口/授权接口/转换来源 |
| AC07 | MF08 | 冻结统计fixture/聚合/报告 |
| AC08 | MF09 | AF/Koa/执行Worker/浏览器 |
| AC09 | MF11 | SQL/索引/撤权/固定对照 |
| AC10 | MF12、MF14 | 用量账/并发/支付与退款 |
| AC11 | MF05、MF10、MF13 | 320px与桌面浏览器 |
| AC12 | MF14、MF15 | 真实身份/渠道/媒体与服务费对账 |
