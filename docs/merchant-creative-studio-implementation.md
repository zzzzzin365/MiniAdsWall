# 商家营销创意工作台：实现与启动

2026-10-13。工作台首版已实现；图片生成和直接视觉理解被严格双模型能力限制阻断。未发布生产环境，未连接真实广告投放。

## 1. 已实现的用户路径

入口：广告管理页现有“创意素材”标签，URL为`/#creatives`。旧`/?studio=1`自动兼容跳转，不再提供独立工作台页面。

选择品牌 → 填主体、目的、idea和已确认事实 → 可选上传图片/视频 → 保存需求 → DeepSeek输出最多3组场景/文案/脚本/投放建议 → 对话修改或确认方案 → 保存文案草稿，或明确确认规格后提交Seedance视频 → 轮询/转存 → 人工审核 → 保存视频草稿。

附带能力：最近会话、刷新恢复、方案修改版本、品牌隔离、独立运营身份、上传原件保留、私有下载、任务排队/租约/崩溃恢复、同键查单、取消未提交任务、已知任务恢复查询、图片请求明确拒绝。上传不会触发媒体生成。

默认投放出价为null。可填写bid_basis的渠道、贡献毛利、点击成交率与广告占比；Python Decimal计算CPC参考上限（整数分），明确为商家假设估算，不执行出价。

## 2. 固定模型与限制

- 文本/对话/创意/脚本/投放建议：`deepseek-v4-1-flash-260910`，`POST /studio/generate`。
- 明确请求视频：`doubao-seedance-1-0-pro-fast-251015`，`POST /studio/video`、`GET /studio/video/{task_id}`。复用原video适配器，并增加固定模型检查。
- 项目保存的供应商模型清单显示当前DeepSeek仅支持text输入。本版上传附件会保存为参考，但不发送给文本模型、不宣称读懂。补充文字后可继续，图片可作为Seedance首帧。
- Seedance不能输出图片；image请求在Koa返回422 unsupported_output，不调用供应商、不自动切换Seedream。
- 当前不做视频编辑、口播、BGM或自动字幕，不宣称经过视觉模型自动QA；由商家审核最终商品外观/文字。
- 首版是结构化对话工作流，生成/审核/媒体操作由Koa持久化授权；不是允许模型自主发布/调价的Agent。

## 3. 启动

沿用根目录`.env`中的ARK_API_KEY/AGENT_SERVICE_TOKEN，以及`apps/mini-ad-wall/server/.env.local`的ADS_MYSQL_URL、运营凭据。不要把凭据写进命令或文档。

在本地配置中设置：

```dotenv
MERCHANT_WORKSPACE_ENABLED=true
STUDIO_VIDEO_ENABLED=true
```

视频有真实供应商费用；不需要生成视频时将STUDIO_VIDEO_ENABLED保持false。

```sh
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm --prefix apps/mini-ad-wall/server run build
# 首先执行原营销迁移到013（已有数据库不会重建旧表）
# 使用现有 demo 启动器加载 .env/.env.local 时会执行营销和工作台迁移
.venv/bin/python scripts/run_studio.py migrate
.venv/bin/python scripts/run_studio.py worker
```

`run_studio.py migrate`要求营销013已应用；启动器 `./demo.sh` 在工作台开关启用时增加019迁移和studio-worker进程管理。现有Koa/Agent进程需要重启以加载新增路由；启动器不会强制结束未由它管理的进程。

Python Agent注册了studio router，沿用内部服务认证。浏览器只请求Koa，不拿ARK凭据。私有文件默认在公开uploads的同级`studio-private`；Worker与Koa必须共用该目录，不能放在公开uploads下。

图片首帧需额外配置STUDIO_PUBLIC_BASE_URL（供应商可访问的HTTPS域名）及至少32字节的STUDIO_ASSET_SIGNING_KEY；签名URL30分钟有效。无配置仍可文生视频。视频输入仅登记/参考，不能作为当前模型的参考视频。

## 4. 存储与迁移选择

019包含版本化`merchant_studio_objects`和任务/供应商回执`merchant_studio_jobs`；对象kind覆盖brief/session/recipe/asset/output/draft。复用营销brand/environment外键与成员权限。

为避免旧营销迁移审计把预留014–018当已完成，019独立登记在`merchant_studio_schema`，显式依赖营销013，并复用现有DDL校验（列、类型、索引、外键、CHECK、checksum）。不改已应用010–013，不把最大版本视为完整能力。

本版采用独立MySQL队列和studio Worker，未复用hosting run/output_ref。文本Schema冻结于contracts/merchant/studio/v1，视频产物保存私有文件。该选择避免未实现AF桥接阻塞本次交付；多节点部署需要共享文件存储，当前没有交付云对象存储/HA。

草稿独立保存为kind=draft、status=unpublished，不调用旧广告创建接口，也不激活marketing_creatives。后续M2需要将草稿关联到受权商品/活动/广告后再发布。

目前有生成任务/供应商ID/规格/hash/授权回执；没有平台收费、真实报价或服务退款账，前端明示视频可能产生方舟费用，不宣称已实现活动包计费。

## 5. 故障操作

- submission_unknown：保留任务，阻断新视频提交。没有task_id时只能在方舟账户核查；本版不提供可猜测他人task_id的浏览器补绑接口。
- 已知provider_task_id查询/下载失败：自动查单最多8轮；失败后`POST /jobs/:id/retry`（confirm_retry=true）只恢复同task查询，不重新提交付费生成。
- 文本failed：同恢复接口可将原任务重新排队；失败不会返回占位成功。
- queued取消：仅取消尚未提交任务；已经提交时返回409，不假称供应商取消成功。
- 关闭新入口后：继续读取、查单、转存和已有供应商任务核对。

## 6. 验证结果

详见[验证报告](validation/merchant-growth-loop/studio/README.md)。本次未新提交付费视频；视频协议/任务完整流程用受控供应商返回和真实MySQL测试。真实Seedance工作台调用、首帧公网读取与30例人工评测仍待验收。


### 2026-10-13：合并现有模块

创意生成、需求输入、方案协同及生成确认并入现有“创意素材”；“广告列表”继续承担广告管理。原独立入口移除，后端模型路由/任务模块保持独立。创意标签保留已有素材浏览，列表筛选/导出/报表仅在广告列表展示。切换标签保留未提交需求；旧studio链接兼容。视觉统一为既有管理页中的双栏工作区，采用8px间距体系、清晰标题层级、控件焦点/禁用态与320px单栏适配。


### 上传自动Seed理解（2026-10-13更新）

用户上传后同事务创建素材理解任务。Worker自动调用Seed（STUDIO_UNDERSTANDING_MODEL，默认doubao-seed-2-0-mini-260428）；图片直接输入，视频本地提取最多8帧带时间点的画面，音频不处理。结果显示主体/画面观察/素材宣称/不确定信息，并交给DeepSeek生成后续创意。新规则覆盖此前双模型限制，Seedance仍只用于明确授权的视频生成。无需新增数据库迁移，分析任务复用text队列并通过asset_analysis类型区分。

代码构建、7项Python协议测试和真实MySQL流程通过。Seed真实图片调用返回ModelNotOpen，当前账号需开通所配置的Seed模型，或配置一个已开通且支持图片理解的Seed模型；没有声称实调成功。


### 广告编辑与素材生成职责调整

广告编辑面板仅保留AI投放助手：推广目标、目标用户、兴趣与策略说明；只请求strategy接口，使用DeepSeek。移除旧标题/文案生成和视频上传入口，保留现有关联视频的只读预览。缺少出价依据时建议金额返回null，不自动修改表单。创意素材面板负责素材理解、创意文本及媒体生成，不再展示投放建议卡或CPC配置。
