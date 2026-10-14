# 商家营销创意工作台 SDD

- 版本：1.2；日期：2026-10-13；状态：首版代码已落地；已通过文本模型、数据库、浏览器验收；视觉理解被能力限制阻断，真实工作台视频和完整人工评测待验收。
- 上位设计：[个人商家闭环 SDD](merchant-growth-loop-sdd.md)。本文件细化 M1/MF02–MF05，并允许在首次投放前输出投放建议，不依赖 M2 报告或 AF 案例库先完成。
- 本次范围：营销主体/目的/idea输入 → 多模态理解 → 对话确认 → 文本建议/图片/视频生成 → 投放建议 → 审核与导出/保存草稿。
- 不包含真实投放、实际出价执行、商业收益承诺；生成创意不自动启动广告。

## 0. 实现记录

详见[实现与启动](merchant-creative-studio-implementation.md)及[验收报告](validation/merchant-growth-loop/studio/README.md)。CS00/01/03/04/06已有代码与检查；CS05视频代码与故障协议通过模拟供应商+真实数据库验收；CS02直接视觉理解不可用；CS07浏览器通过但真实视频/30例人工评测未完成。

首版采用独立studio MySQL队列/Worker和私有文件，非hosting/AF桥接；初期草稿独立保存，不发布到旧广告。收费台账、云存储/HA均未实现。后续正文为目标设计，超出首版部分不能当作已完成。

## 1. 模型职责与现有能力

本版本按用户指定采用双模型固定路由。切换发生在每次工具/API调用，不能修改全局环境变量或让一个用户的任务改变其他会话的模型。

| 能力 | 固定模型 | 能力边界 |
|---|---|---|
| 对话、Agent规划、商品/素材理解、创意文本、脚本、投放建议、结果解释 | DeepSeek-V4.1-Flash：`deepseek-v4-1-flash-260910` | 文本能力已有本项目预检证据；视觉/视频与工具调用按实际适配器独立验收 |
| 用户明确请求生成视频 | Doubao-Seedance-1.0-pro-fast：`doubao-seedance-1-0-pro-fast-251015` | 当前仅文字/首帧生视频 |
| 用户明确请求生成图片 | 媒体路由目标同为Seedance；能力检查阻断 | 该模型没有当前可用的图片输出接口；返回unsupported_output，不提交供应商、不计费 |

图片生成目标保留，但在当前双模型约束下不能交付。关闭图片生成入口，允许DeepSeek生成图片创意说明/提示词文本；这不算生成图片。只有用户另行授权支持图片输出的模型并完成适配验收后才能开放，不自动调用Seedream。

配置：`STUDIO_DEFAULT_MODEL=deepseek-v4-1-flash-260910`、`STUDIO_MEDIA_MODEL=doubao-seedance-1-0-pro-fast-251015`、`STUDIO_ROUTING_POLICY=strict-two-model-v1`；共用服务端ARK凭据/base URL。禁止普通会话通过参数覆盖模型，禁止Seedance失败后改用DeepSeek假装媒体生成成功。

DeepSeek素材理解必须验证实际模型与接口是否接受图片/视频，现有文本客户端预检不能证明视觉能力。若不支持原视频，使用有界抽帧且验证图片输入；若图片理解也不可用，返回capability_unavailable并请求商家补充文字，不偷偷调用第三种理解模型。OCR/ASR属于独立预处理能力；涉及第三方模型API默认禁用，启用需用户另行授权；本地提取注明工具来源，不称DeepSeek直接理解声音。

官方资料：[图片理解](https://docs.volcengine.com/docs/ark/image-understanding?lang=zh)、[视频理解](https://docs.volcengine.com/docs/ark/video-understanding?lang=en)、[图片生成](https://docs.volcengine.com/docs/ark/image-generation-api?lang=en)、[视频生成任务](https://ark.volcengine.com/region:cn-beijing/docs/ark/create-video-generation-task-api?lang=zh)。本项目已接能力以 `api/video.py`、[接入说明](seedance-video-integration.md) 为准，不能据新官方版本宣称旧适配器已升级。

## 2. 用户输入与低门槛流程

营销主体不只商品，支持 product/service/store/event。广告主可以仅说“帮我推广这根牙刷”，也可以提供完整广告图片/视频。至少有非空文字或一个已登记资产；不强制用户先建立完整商品档案。

Brief：subject_type、subject_description（可空）、objective（可空）、idea（可空）、asset_refs、output_types、constraints。objective 支持 awareness/traffic/leads/appointments/sales/undecided；constraints含品牌语气、渠道/尺寸、预算、禁用内容、保留要求。文字总长≤10000字，主体待识别时subject_type可undecided。

资产角色需广告主可选标记：subject_photo/existing_ad/style_reference；不确定则系统提出候选，请商家确认。风格参考的宣称不能写进商品事实。默认保留原件，不覆盖已有素材。

输出类型可独立选择：creative_advice/image/video/delivery_advice。默认先给免费或已有额度内的文本预览；图片/视频展示预计用量、方案和规格后，由用户明确点击生成。若初始请求已经明确选择媒体输出且确认同版本价格/额度，则无须再次确认同一任务。

交互顺序：

1. 商家发文字/照片/现成广告；Koa保存brief revision与附件归属。
2. Agent调用理解工具，返回看到了什么、现有广告讲了什么、缺什么，标证据来源。
3. 只对影响事实正确性/输出的关键缺项追问，每轮最多3项。可先给假设性方案；缺价格不妨碍生成无价格创意，缺品牌不妨碍通用草稿。
4. 形成最多3个创意方向；商家选择/修改，冻结recipe。
5. 独立生成所选文本/图片/视频；完成一个先展示一个，失败不拖垮其他输出。
6. 商家查看并修改；确认素材和商品事实后保存待审核素材、导出或创建试投草稿。

聊天侧边栏与结构化卡片同步：事实确认卡、创意方案卡、生成任务卡、投放建议卡。刷新从数据库恢复，不以聊天历史猜业务状态。显示操作与等待结果，不显示内部思维链。

## 3. 内容理解：商品、画面与营销信息分开

复用上位资产限制：≤5张图或1段≤60秒视频；预处理≤24帧，图像坐标/视频时间戳可追溯。模糊、遮挡、主体太小先报告质量问题，不自动猜型号。

理解结果 schema：

- subject_candidates：类型/名称候选、可见形态与证据，未知保留unknown。
- observations：颜色、外形、包装可见字、画面场景；证据含asset/hash/bbox或time。
- claims：已有广告声称什么，status=source_claim/unverified/merchant_confirmed；识别出“杀菌99%”不代表验证为真。
- ad_structure：开头吸引点、主体展示、卖点、优惠、CTA、节奏及可定位问题。
- quality_issues：模糊/光线/遮挡/音画不同步/信息缺失。
- conflicts/missing_fields：文字与照片冲突、价格与版本冲突、完成某输出所必需的缺项。

主体照片只支持“看起来是一根牙刷，白色手柄”等观察；不能证明软毛、抗菌、医生推荐。商家提供的“软毛”记录source=merchant，不称第三方验证。模特、背景、包装和字幕识别内容不自动当作营销主体。

纯文本路径跳过媒体分析，保留商家文本证据；没有图片时可给示意图提示词文本，但当前策略不输出生成图片。分析抽样不能声称逐帧读完视频；音频未处理时明示。

## 4. 创意方案、生成配方与产物

CreativeRecipe不可变版本：brief_revision、subject/product_revision、understanding_ref/hash、scenario、hook、supported_benefits、CTA、tone、reference_assets/roles、preserve_constraints、claim_evidence、output_spec、cost_quote_ref。

三种现成素材操作：diagnose（只分析）、rewrite（改文案/脚本）、remake（生成新版本）。旧Seedance不支持video edit，remake只根据已确认分析重做视频，不宣称对原视频直接精准编辑。

### 4.1 创意文本

每方向输出“用户场景、核心表达、标题/正文、视觉说明、分镜、CTA、理由、证据、假设与缺项”。建议描述可以解释潜在诉求，不能假称识别了真实受众行为。脚本含镜头时间段、画面、屏幕文字、口播及素材引用；总时长必须匹配选择的规格。

### 4.2 图片

当前严格双模型策略下图片输出为blocked_capability。用户要求图片时先在Koa返回422 unsupported_output，说明Seedance只支持视频；可以继续返回DeepSeek图片创意文本，但不能把提示词、视频帧或原上传图冒充新生成图片。以下QA要求保留为未来图片能力接入的验收标准。

价格、优惠截止日期和关键声明优先通过后期确定性排版添加，并保存图层/字体/文案版本；避免模型生成乱码或改价。检测错字、商品外形偏差、Logo/包装变形、主体丢失，展示与原图对比；检测只是辅助，发布前商家确认。

### 4.3 视频

当前默认5秒/9:16/480p，允许范围由现有VideoInput决定（2–12秒）。流程可选择：已确认照片→合适的首帧→Seedance；或者直接文本→Seedance。禁止为视频任务自动调用额外图片模型生成首帧。没有首帧也可文生视频，但商品外观一致性标未保证。

视频上传路径用于理解、诊断和重写脚本；不能将上传视频直接传进当前Seedance接口。口播、字幕、BGM均为独立后期能力，首版字幕可确定性渲染，口播/BGM默认关闭，明确最终成片是否有声音。商品比例/包装/字体/声明和镜头符合度接受人工审核。

输出后下载供应商有效期内结果到受控对象存储，校验真实格式、大小/hash，再发asset_id。下载来源仅限可信供应商返回地址，执行DNS/重定向/内网限制；不能给任意模型输出URL开放抓取。URL过期或下载失败置materialization_failed，不冒称可交付。生成成功但未交付的补偿规则引用固定报价，默认不消耗商家交付额度，供应商成本仍记录；恢复只下载，不重新生成。

## 5. 投放建议：目标与出价必须有口径

首次投放建议不强制report_id；来源分首次hypothesis、历史reference、当前report_evidence。复用实际可读历史/报告时绑定hash，缺权限时不查。创意输出可完成，投放建议因缺预算返回needs_input，两者互不阻塞。

字段：recommended_objective、audience_hypotheses、placements、creative_refs、test_plan、budget、bid、rationale、evidence_refs、missing_data、status。objective与商家目的不一致时解释并要求确认；“销量”缺购买入口/订单回传时先补齐，不伪称已经支持成交优化。

bid：channel、billing_model=CPC/CPM/CPA/unknown、currency、amount_minor（可空）、range_minor（可空）、basis、applicability、status。无渠道规则、预算、转化率或成本依据时amount_minor=null，提供试投方法，不让模型硬填1–100。禁止混淆旧广告price与媒体竞价。

给定单位贡献毛利M、点击购买转化率p和允许用于广告的比例a，可给CPC参考上限floor(M×p×a)，M用整数分、p/a取0–1；这是基于假设的上限估算，不是拍卖最优价。无历史p时必须用户输入明确假设范围且标estimate；有效历史样本的p只在同商品/渠道/窗口可比时引用。转化率为0不自动推出应出价0，返回insufficient_basis。CPM/CPA另有专用口径，首期没有可靠依据时不计算。

建议输出仅draft，不修改广告、预算或bid。后续交给上位试投/提案执行；真实渠道参数范围校验由渠道adapter完成。旧 `Strategy.bidSuggestion:float[1,100]` 保持旧客户端兼容，新链路不使用该结构作为可信投放契约。

## 6. Agent规划与人机协同

采用有界工具编排，首版不引入多个自治子Agent。单轮最多8次工具调用、2次模型格式修复；生成任务与等待轮询不计作连续模型循环。超过限制保存中间产物并needs_input/failed。

工具白名单：read_brief、analyze_assets、read_confirmed_facts、propose_scenarios、build_recipe、generate_copy、request_image_job、request_video_job、draft_delivery_plan。历史检索为可选，M1无历史仍能工作。不存在publish_ad/set_live_bid工具。

工具请求通过Koa校验任务归属、输入版本、asset角色、用户授权和用量额度；模型不能自行确认商品事实、替用户批准付费生成或审核素材。agent proposal与merchant decision追加保存accept/reject/modify及理由，修改生成新recipe revision。

会话状态：collecting → analyzing → needs_confirmation/plan_ready → generating → review_ready → completed；部分产物失败用partial展示。job状态复用queued/running/succeeded/failed/cancelled/submission_unknown。每种状态变化有actor/cause/revision。

合作期间输入变更不会篡改正在跑的冻结任务；完成结果标based_on_older_brief，不替换当前方案。需要新任务时单独确认用量。取消禁止新步骤，已提交供应商任务继续查终态；界面明确“停止后续步骤”与供应商取消结果。

任务请求receipt持久化先于供应商提交，保存client_job_id、input_hash、authorization_ref、provider_task_id、output_ref、cost/usage、model/spec版本。不把X-Request-ID当上游幂等。收到task_id后只查询同task；未知提交不能自动创建替代任务。

## 7. Koa接口与持久化

前缀 `/api/merchant/v1/studio`；身份和环境沿用上位设计。写接口mutate+Idempotency-Key，所有引用同scope。产品未建档可先存brief，商家确认后转换为product revision，不能模型直接创建可信商品事实。

| 接口 | 请求/结果 |
|---|---|
| POST /briefs | 文字、目的/idea、资产角色、输出意愿；201 brief_id/revision |
| POST /briefs/:id/revisions | expected_revision及修改；201新revision |
| POST /sessions | brief_id/revision；201 session_id，绑定subject |
| POST /sessions/:id/turns | expected_revision、message、结构化decision；202 turn_id/run_id |
| GET /sessions/:id | 当前业务状态、产物、待确认项；分页turn，不暴露他人产物 |
| POST /recipes/:id/decisions | expected_revision、accept/reject/modify、修改字段；新revision/确认结果 |
| POST /recipes/:id/jobs | output_type、spec、quote_ref、authorization_ref；202 job_id |
| GET /jobs/:id | 状态/任务回执/已入库资产；不返回服务凭据 |
| POST /outputs/:id/review | revision、approve/reject及理由；审核结果，不自动发布 |
| POST /outputs/:id/save-draft | 审核资产和主体版本；201现有creative草稿引用 |

Schema位于 `contracts/merchant/studio/v1/`，独立于旧GenerationInput。JSON限制64KiB；正文10000字；结果长脚本/分析存output_ref。输出类型有限枚举，未知字段拒绝。

新增逻辑对象：studio_briefs/revisions、studio_sessions/turns、creative_recipes/revisions、creative_jobs/provider_receipts、studio_outputs/reviews。复用上位media_assets/evidence/product/creative_provenance和托管runs；MF01冻结物理映射后合入019基础迁移，不单独抢新编号。每job唯一output_slot，回写验证lease/revision，provider_task_id在provider+account范围唯一。

服务新落点：`server/services/merchant/studio/`负责brief/recipe/权限/回执/保存草稿；`marketing/merchant/studio/`负责planner/understanding/copy/delivery_plan与provider adapters；`client/src/components/merchant/studio/`负责对话及结构化卡片。旧创意/策略接口不破坏，旧客户端不获得新生成写权限。

## 8. 开发任务与验收

| 任务（全部pending） | 依赖/改动 | 通过门槛 |
|---|---|---|
| CS00 基线/能力 | MF00/01；Schema、能力矩阵、quote与资产权限 | 无视觉能力不会把图片降成无说明纯文本；旧接口兼容 |
| CS01 Brief/会话/recipe | MF02；上述Koa接口与迁移映射 | 纯文本/单牙刷照片/已有广告均可起步；刷新可恢复，越权拒绝 |
| CS02 多模态理解 | MF03；抽帧/ASR/视觉、证据 | 照片观察与宣传声明分开；模糊/冲突/音频缺失准确标记 |
| CS03 对话规划/文本 | CS02、纯文本可绕过分析 | 每轮≤3问题；≤3方向；修改有版本；不编功效/价格 |
| CS04 图片能力阻断 | CS00、CS03 | 图片请求422；供应商调用数/用量消耗均为0；不自动换模型 |
| CS05 视频生成 | CS03、现有Seedance | 文生/首帧路径通过；视频输入不进入不支持接口；同task查单 |
| CS06 投放建议 | CS03；可选report读取 | 无依据bid=null；CPC上限有单位/假设；无广告写操作 |
| CS07 UI/整体验收 | CS01–06、MF05 | 文字/照片/已有视频三路径全链，部分失败/撤权/断网恢复 |

冻结至少30例：纯文字、简陋主体照片、遮挡模糊照片、多个主体、既有广告图、音视频、虚假功效、价格冲突、无预算/渠道各有覆盖。人工标注主体、可见事实、原素材宣称和应追问项。测理解事实错误、无依据广告声明、原商品保持、商家可用率、单次编辑时间、耗时/成本；模型评分不能替代人工审核。

至少一个“牙刷”端到端案例：白色牙刷照片＋推广目的＋通勤idea → 仅提取外观观察 → 商家确认价格/真实卖点 → 两个场景方案 → 图/5秒视频选择生成 → 结果转存 → 投放建议bid=null或明确假设CPC上限 → 审核存草稿。不得凭照片生成“抗菌99%”。另一个现成视频案例验证时间定位和重做，不能声称当前模型直接编辑原视频。

真实MySQL验证同job并发提交、任务完成回写、旧版本拒绝和用量唯一；mock只证明故障控制。真实模型需分别记录DeepSeek理解/文本与Seedance视频成功和成本；图片仅验证明确阻断，不能宣称图片生成通过，不因一个接口成功声称所有能力接通。验收证据存 `docs/validation/merchant-growth-loop/studio/`。

## 9. 发布与停用

继承上位权限/开关；增设独立STUDIO_IMAGE_ENABLED/STUDIO_VIDEO_ENABLED，能力或额度未就绪仅关闭对应按钮，文本和已完成结果继续可读。M1可先交付文本理解/创意，图片/视频分别验收后启用，CS07可验收严格双模型版本，但必须报告图片生成未交付、视觉理解实际状态；不能宣传完整图片/视频生成能力。

停用后禁止新提交，继续查单/转存/用量核对；兼容回滚保留brief/产物/receipt。首次付费供应商提交和媒体导出权限采用已有商家授权，不要求重复确认同一冻结请求。

## 10. 自动API路由与显式生成授权

### 10.1 触发规则

业务意图由DeepSeek解析为结构化候选，Koa确定是否获准执行。关键词不能直接触发Seedance。

| 用户操作 | 路由结果 |
|---|---|
| 上传牙刷照片，问怎么推广 | DeepSeek理解/文本；Seedance调用0次 |
| 上传广告视频，要求分析或优化建议 | DeepSeek分析/脚本；Seedance调用0次 |
| “写一个视频脚本”“给图片营销建议” | DeepSeek文本；Seedance调用0次 |
| “暂时不要生成视频”“多少钱”“能不能生成” | DeepSeek答复；不提交媒体任务 |
| “按这个方案生成5秒广告视频”/点击生成视频 | Seedance视频任务；文本规划仍为DeepSeek |
| “生成图片” | 候选media=image；能力检查后422，供应商调用0次 |
| “生成图片和视频” | 图片blocked；分别显示状态，已明确授权的视频可以独立执行 |
| 完成视频后“解释一下并建议出价” | 立即使用DeepSeek；无新Seedance提交 |

initial brief的output_types仅表示意愿，不能充当任意未来任务授权。授权记录来自真实用户消息或按钮，绑定actor/session/source_turn、output_type、recipe_revision/hash、spec、quote/额度上限、expires_at。只有输入明确请求具体生成，且用量/规格符合已确认范围时成立；模糊意图生成needs_confirmation卡片。用户已授权同一冻结任务无需再次确认。

模型输出、OCR、原广告中“生成视频”字样、历史消息和检索案例均不得创建授权。上传附件不是生成授权。Koa检查身份、版本、授权、支持能力和额度后才允许request_video_job。

### 10.2 调用边界与恢复

新增 `marketing/merchant/studio/model_router.py`（纯决策/能力校验）、`server/services/merchant/studio/generation-authorization.ts`。Router输入task_kind、requested_output、validated_authorization_ref、capability_manifest；返回provider/model/api_operation/route_reason/policy_version，或结构化blocked错误。

DeepSeek走方舟Chat API适配器；Seedance走现有POST/GET /contents/generations/tasks。视频状态查询是已授权任务的必要后续，不需新用户请求；查询结果解释仍走DeepSeek。不得每次轮询重新创建视频任务。

路由信息与任务input_hash同存；重试使用冻结模型/输入/策略，不因为当前配置变更切换供应商。新任务采用新策略版本。批量任务逐输出独立job/授权/回执；一个失败不补交其他已成功job。

默认严格无模型fallback；DeepSeek不可用返回503，Seedance超时按submission_unknown查单，不自动尝试其他模型或自动新建任务。共享ARK凭据不意味着共享模型能力或输入格式。

### 10.3 开发与验收补充

CS00冻结strict-two-model-v1契约和能力矩阵；CS03增加意图候选/追问；CS04验收图片阻断；CS05增加Koa生成授权/Router/receipt；CS07覆盖下列用例：

- 所有非生成消息仅调用DeepSeek，含图片/视频附件、否定句、疑问句、提示注入和脚本请求。
- 用户授权生成视频只产生一个Seedance提交；重复按钮、跨进程重放、超时均不新增任务；之后文本重新走DeepSeek。
- 图片请求被阻断且不扣额度；混合请求分项可见，无Seedream或第三模型调用。
- 伪造授权、过期授权、旧recipe、撤权、错误scope拒绝；route日志记录理由/版本/输入hash，不记录密钥。
- DeepSeek视觉能力用真实图片/帧测试，单独记录是否可用；不支持时显式降级到商家文字输入。

证据：`docs/evidence/deepseek-v41-flash-topk-experiment.md`与`ark-deepseek-models.json`仅证明本项目记录的模型ID和文本预检，不证明多模态适配已完成。设计无需更改当前.env或api路由代码。


### 2026-10-13：合并现有模块

创意生成、需求输入、方案协同及生成确认并入现有“创意素材”；“广告列表”继续承担广告管理。原独立入口移除，后端模型路由/任务模块保持独立。创意标签保留已有素材浏览，列表筛选/导出/报表仅在广告列表展示。切换标签保留未提交需求；旧studio链接兼容。视觉统一为既有管理页中的双栏工作区，采用8px间距体系、清晰标题层级、控件焦点/禁用态与320px单栏适配。


### 上传自动Seed理解（2026-10-13更新）

用户上传后同事务创建素材理解任务。Worker自动调用Seed（STUDIO_UNDERSTANDING_MODEL，默认doubao-seed-2-0-mini-260428）；图片直接输入，视频本地提取最多8帧带时间点的画面，音频不处理。结果显示主体/画面观察/素材宣称/不确定信息，并交给DeepSeek生成后续创意。新规则覆盖此前双模型限制，Seedance仍只用于明确授权的视频生成。无需新增数据库迁移，分析任务复用text队列并通过asset_analysis类型区分。

代码构建、7项Python协议测试和真实MySQL流程通过。Seed真实图片调用返回ModelNotOpen，当前账号需开通所配置的Seed模型，或配置一个已开通且支持图片理解的Seed模型；没有声称实调成功。


### 广告编辑与素材生成职责调整

广告编辑面板仅保留AI投放助手：推广目标、目标用户、兴趣与策略说明；只请求strategy接口，使用DeepSeek。移除旧标题/文案生成和视频上传入口，保留现有关联视频的只读预览。缺少出价依据时建议金额返回null，不自动修改表单。创意素材面板负责素材理解、创意文本及媒体生成，不再展示投放建议卡或CPC配置。
