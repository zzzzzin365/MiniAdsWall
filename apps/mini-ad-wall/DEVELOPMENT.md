# Mini Ad Wall 项目开发文档

## 项目概述
**Mini AD WALL** 
完成了所有进阶任务,
此外，出于我对《广告知识学习》中提到的相关真实广告系统的理解与实际可用开发时间，
功能上，加入数据可视化看板模块、LLM广告文案生成、投放策略建议；性能优化上，本项目重点围绕“高频点击统计”和“广告列表扩展性”做了专项设计：一方面通过**点击计数缓冲写入**避免每次点击都同步落盘，降低 I/O 压力；另一方面通过**广告列表组件封装**预留列表虚拟化能力，为广告量级从几十条平滑扩展到上千条做好架构准备。
最后，由于我自身对UI设计也感兴趣，故在完成本课题作业要求的同时，我基于B端管理系统的实际业务场景，从原型图出发进行了合理的UI、UX设计，使该mini 广告墙具备清晰的操作流程与高效的交互体验。

项目地址：
https://github.com/zzzzzin365/MiniAddwall

项目演示见演示视频。

注：上传视频属于本地运行时素材，GitHub 仓库仅保留 uploads 目录占位文件；如需演示视频播放，可在 server/uploads 中自行放入 .mp4 或 .mov 文件。

### 核心特性
- 工程化架构：模块化、服务化、高内聚低耦合的精简版
- 广告 CRUD 管理：完整的增删改查功能
- 智能排序算法：基于出价和点击量的动态排序
- 点击统计：实时记录广告点击量
- 视频上传：支持多视频上传和管理
- 动态表单渲染：基于 JSON Schema 的表单配置
- LLM与业务需求结合：广告文案生成、投放策略建议
- 数据可视化看板：多维度广告效果与出价关系分析
---

## 技术选型

### 前端技术栈

| 技术 | 版本 | 用途 | 选型理由 |
|------|------|------|----------|
| **React** | 18.2.0 | UI 框架 | 组件化开发，生态成熟 |
| **Vite** | 4.4.5 | 构建工具 | 快速热更新，开发体验好 |
| **Axios** | 1.6.0 | HTTP 客户端 | 简洁的 API，支持拦截器 |
| **Chart.js + react-chartjs-2** | 4.x / 5.x | 图表渲染 | 轻量级、满足柱状图/折线图/散点图需求 |
| **TypeScript** | 5.3.2 | 类型系统 | 提升可维护性与可读性 |
| **原生 CSS** | - | 样式方案 | 轻量级，无额外依赖 |

### 后端技术栈

| 技术 | 版本 | 用途 | 选型理由 |
|------|------|------|----------|
| **Node.js** | - | 运行时 | JavaScript 全栈，开发效率高 |
| **Koa2** | 2.14.2 | Web 框架 | 轻量级，支持 async/await，洋葱模型 |
| **koa-router** | 12.0.0 | 路由管理 | 简洁的路由定义 |
| **koa-body** | 6.0.1 | 请求体解析 | 支持 JSON、表单、文件上传 |
| **@koa/multer** | 3.0.0 | 文件上传 | 处理 multipart/form-data |
| **koa-static** | 5.0.0 | 静态文件服务 | 提供视频文件访问 |

### 数据存储

- **JSON 文件**：轻量级数据持久化（`data.json`）
- **文件系统**：视频文件存储在 `uploads/` 目录

### LLM 服务

| 技术 / 服务 | 用途 | 选型理由 |
|-------------|------|----------|
| **OpenRouter + mistralai/devstral-2512:free** | LLM 广告创意生成、投放策略建议 | 通过 OpenRouter 作为统一网关接入多模型，免维护大模型基础设施；结合业务约定强制输出 JSON，方便与广告表单和策略结构对接 |
| **MiniAdsWall Agent / AdsAgent** | 广告运营对话助手、广告数据工具分析、RAG 知识库问答、多 Agent 分流 | MiniAddwall 通过 `ADS_AGENT_API_URL` 调用 MiniAdsWall Agent 的 `/chat` 与 `/health`；后端转发结构化 `ads` 数据，MiniAdsWall Agent 调用 `ads_summary`、`ad_performance_search`、`bid_simulation` 后交给 AdsAgent 回答 |

---
## MiniAdsWall Agent 深度融合运行方式

本项目已接入 `/api/ai/assistant/chat`。前端右下角“AI”按钮会把当前广告列表传给 Koa 后端；后端以结构化 `ads` 字段转发给 MiniAdsWall Agent，而不是只拼接一段摘要 prompt。

MiniAdsWall Agent 收到请求后会：

1. 通过意图识别判断是否属于 `ad_optimization`、`creative_generation`、`bid_strategy` 等广告意图。
2. 将广告意图路由到 `AdsAgent`。
3. 调用广告工具：
   - `ads_summary`：汇总广告数量、总点击、平均出价、Top 广告、无素材广告。
   - `ad_performance_search`：找出高出价低点击、低出价高点击、无素材等重点广告。
   - `bid_simulation`：模拟小幅加价后的排序分数并给出加价/改素材建议。
4. 检索广告知识库 RAG，例如排序规则、出价优化、素材 A/B 测试、文案优化和风险边界。
5. 将工具结果和知识库结果注入 AdsAgent 上下文后生成回答。

若 MiniAdsWall Agent 未启动，MiniAddwall 后端会自动返回本地兜底分析，不影响广告 CRUD、上传和看板。

### 启动顺序

1. 启动 MiniAdsWall Agent，确保 `http://localhost:8000/health` 返回正常。
2. 启动 MiniAddwall 后端：

```bash
cd server
ADS_AGENT_API_URL=http://localhost:8000 npm run dev
```

3. 启动 MiniAddwall 前端：

```bash
cd client
npm run dev
```

4. 打开 `http://localhost:5173`，点击右下角 AI 按钮。

### 联调检查

```bash
curl http://localhost:3001/api/ai/assistant/status
```

若返回 MiniAdsWall Agent 在线，AI 助手请求会进入 MiniAdsWall Agent 的 AdsAgent、广告工具、记忆和知识库链路。

---
## 自行开发的核心功能

| 功能模块 | 说明 | 位置 |
|---------|------|------|
| **广告 CRUD 管理（任务1）** | 实现创建、编辑、删除、查询广告列表、点击次数 +1 五个核心接口，以及对应的业务校验与数据落盘 | 接口：`server/routes/ads.routes.ts`；服务：`server/services/ads.service.ts`；模型：`server/models/ads.model.ts` |
| **广告排序算法** | 基于出价和点击量的动态排序，公式：`score = pricing + (pricing × clicks × 0.42)`，使列表展示更贴近真实竞价场景 | `server/services/ads.service.ts` |
| **点击统计系统** | 实时记录广告点击量并影响排序，为后续 CTR、转化率等指标打基础 | `server/services/ads.service.ts` |
| **多视频上传与素材管理（任务2）** | 支持为单条广告上传多段视频素材且有前端校验（校验格式 .mp4/.mov、大小 50MB），统一存储到 `uploads/`，并返回可访问 URL 与广告实体关联 | 上传服务：`server/services/upload.service.ts`；控制器：`server/controllers/ads.controller.ts`；前端上传与校验：`client/src/components/AdModal.tsx` |
| **视频随机播放（任务2）** | 用户点击广告卡片时，从该广告的 `videos` 数组中随机选择一个视频进行播放，模拟真实多素材 AB 测试场景 | 播放入口：`client/src/components/AdCard.tsx`；随机选择与弹窗：`client/src/App.tsx`；播放器：`client/src/components/VideoModal.tsx` |
| **动态表单渲染（任务3）** | 基于后端下发的 JSON Schema 配置驱动广告表单自动生成，便于后续扩展字段而无需改动前端代码 | 配置：`server/config/formConfig.ts`；服务：`server/services/form.service.ts`；前端渲染：`client/src/components/AdModal.tsx` |
| **表单校验（任务3）** | 支持必填、长度、正则、数值范围、数组长度等多种校验规则，与 Schema 中的 validator 字段强绑定 | `client/src/components/AdModal.tsx` |
| **数据持久化层** | 基于 JSON 文件的内存缓存 + 同步写入机制，在不引入数据库的前提下模拟简单的持久化层 | `server/models/ads.model.ts` |
| **点击计数性能优化（缓冲写入）** | 针对高频点击场景，将点击计数改为“内存累加 + 定时批量写入 data.json”，避免每次点击都同步写盘，降低 I/O 瓶颈 | `server/models/ads.model.ts`（`incrementClicks`、`flushClicks`） |
| **API 接口封装** | 统一的前端 HTTP 请求封装，屏蔽具体 URL 与 HTTP 细节，让业务代码直接面向广告实体 | `client/src/api.ts` |
| **中间件系统** | 错误处理、请求日志、响应时间统计（洋葱模型），便于排查问题和观察性能 | `server/middlewares/` |
| **广告列表组件封装（预留虚拟化能力）** | 将广告列表渲染抽象为 `VirtualAdGrid` 组件，当前使用 CSS Grid 渲染，后续可无感替换为虚拟化实现以支撑更大广告量级 | `client/src/components/VirtualAdGrid.tsx` |
| **LLM 广告创意与策略服务（扩展）** | 基于 OpenRouter + LLM，为广告主生成标题、文案、关键词以及投放策略建议，并在前端以操作面板形式集成 | LLM 服务：`server/services/llm.service.ts`；AI 业务：`server/services/ai.service.ts`；AI 控制器与路由：`server/controllers/ai.controller.ts`, `server/routes/ai.routes.ts`；前端入口：`client/src/components/AdModal.tsx` |

结合“用户实际操作路径”，按模块补充核心流程与设计思考：

### 1. 广告 CRUD 管理（业务骨架）

- 用户操作：
  1. 打开页面时，前端通过 `GET /api/ads` 调用 `getAds`，由 `ads.service.getSortedAds` 从 `ads.model` 取出所有广告并按业务排序返回。
  2. 用户点击“新建广告”，在动态表单中填写完成后，前端调用 `POST /api/ads`，由 `ads.service.createAd` 完成必填校验并委托 `ads.model.create` 生成广告实体、写入 `data.json`。
  3. 用户在卡片右上角选择“编辑”，表单回填现有广告数据，提交时通过 `PUT /api/ads/:id` 更新，内部由 `ads.service.updateAd` 调用 `ads.model.update` 完成变更与落盘。
  4. 用户点击“删除”，前端弹出确认框，确认后调用 `DELETE /api/ads/:id`，由 `ads.service.deleteAd` 调用 `ads.model.remove` 从内存与 `data.json` 中物理删除。
- 设计思考：
  - 将 CRUD 逻辑集中在 `ads.service.ts`，让控制器只负责 HTTP 协议层，模型只负责数据读写，形成“控制器 → 服务 → 模型”的清晰职责链。
  - 广告实体结构（price、clicks、videos 等）完全由自研代码定义，这保证了后续出价排序、点击热度和素材分析都有统一的数据基础，而不是依赖第三方库的黑盒模型。

### 2. 出价排序逻辑（广告系统的灵魂）

- 核心实现：
  - 在 `ads.service.calculateScore` 中实现评分函数 `score = pricing + (pricing × clicks × 0.42)`，单独抽成纯函数，便于后期扩展。
  - `getSortedAds` 在每次返回列表前，对内存中的广告数组按 score 进行降序排序。
- 用户操作触发链路：
  1. 用户每次打开列表页面或执行创建、编辑、删除操作后，前端都会重新请求 `GET /api/ads`。
  2. 服务层在返回列表前重新计算每个广告的 score，保证用户看到的顺序始终是最新的竞价结果。
  3. 当用户点击广告（见点击统计模块），clicks 增加，会间接抬高 score，使“既高价又有反馈”的广告获得更好排名。
- 思考与扩展：
  - 目前使用一个简单但体现业务理解的“出价 + 出价×点击×系数”模型，模拟现实中的“出价与效果综合排序”。
  - 排序逻辑明确放在 service 层而不是控制器或模型中，是为了将“竞价规则”作为一个独立的业务策略模块，未来可以很自然地扩展为 eCPM、CTR 或二价拍卖，不影响存储与接口层。

### 3. 动态广告创建表单与校验（Schema 驱动）

- 用户操作链路：
  1. 用户点击“新建广告”，前端不会写死表单结构，而是先请求 `GET /api/form-config`。
  2. 后端在 `form.service` 中返回一份 JSON Schema 风格的配置（字段名、展示名称、组件类型、placeholder、校验规则等）。
  3. 前端的 `AdModal.tsx` 遍历配置，按 `component` 字段映射到不同控件（Input、Number、Textarea、VideoUpload），动态生成表单。
  4. 用户输入时，表单值与配置中的 `field` 一一对应存入 `formData`，配合 `validateFormData(formData, formConfig)` 实现 required、maxLength、pattern、min/max、maxCount 等多种校验。
  5. 只有在本地校验通过后，前端才会调用广告创建/编辑接口，避免无效请求。
- 思考：
  - 在真实广告系统中，广告类型经常扩展（信息流、搜索广告、品牌广告等），字段不可能写死。因此将字段抽象为 Schema，是向“可扩展广告平台”靠拢的一次练习。
  - 将校验规则放在配置中，而不是散落在组件内部逻辑里，方便后续根据业务调整字段限制，而不必频繁改动前端代码。

### 4. 视频上传与素材管理

- 用户操作链路：
  1. 在“新建/编辑广告”弹窗中，用户在“上传视频”区域选择本地文件。
  2. 前端使用 `FormData` 将文件通过 `POST /api/upload` 发送给后端，由 `@koa/multer` 负责解析 multipart/form-data，业务逻辑由 `upload.service.ts` 接管。
  3. 上传服务生成带时间戳和随机数的文件名，保存到 `uploads/` 目录，同时返回可访问的 URL。
  4. 前端收到 URL 后，将其写入当前广告的 `videos` 数组，同时在 UI 中展示视频缩略图，允许用户删除某个素材。
  5. 用户提交广告表单时，广告实体中已经包含了与该广告绑定的一组视频 URL。
- 设计思考：
  - 文件上传只借助 multer 完成底层流处理，真正的业务实体（视频列表、与广告的绑定关系）完全由自研逻辑维护，确保对“广告素材”这一概念有自己的业务建模。
  - 文件名生成策略保证幂等性和唯一性，方便后续做日志追踪与问题排查。

### 5. 广告点击热度统计

- 用户操作链路：
  1. 用户点击某一条广告卡片，前端既会弹出视频播放弹窗，也会调用 `POST /api/ads/:id/click`。
  2. 后端的 `ads.service.clickAd` 调用 `ads.model.incrementClicks`，在内存中原子性地将该广告的 `clicks` 加一，并同步写回 `data.json`。
  3. 点击接口返回最新点击数，前端随后刷新广告列表和数据可视化看板，使新的热度立即体现在排序与图表中。
- 思考：
  - 点击统计是现实广告系统中 CTR 计算的第一步，本项目通过手写这一链路，练习了事件触发、统计更新与可视化联动的完整闭环。
  - 将点击更新与排序、图表解耦：点击服务只关心点击数，排序和可视化各自按需读取最新数据，这样后续可以很容易替换排序策略或图表形态。

### 6. 数据可视化指标逻辑

- 核心指标：
  - 出价分布（价格区间内广告数量），对应竞价密度分析。
  - 排名 vs 出价关系，检查排序机制的合理性。
  - 广告点击热度趋势，对比同一广告集内的效果差异。
  - 视频时长 vs 点击热度，探索素材长度与效果的潜在关系。
- 计算位置与思考：
  - 由于当前数据量较小，所有统计指标都在前端 `DataDashboard.tsx` 中通过 `useMemo` 计算，避免不必要的重复运算。
  - 在真实生产环境中，部分聚合可以下沉到后端（例如按照时间窗口聚合点击趋势），本项目通过前端实现先把“指标定义”和“分析视角”理清楚，为未来迁移到后端统计做好准备。

### 7. 性能优化：点击计数缓冲与列表渲染扩展性

- **点击计数缓冲写入（后端）**
  - **业务动机**：广告墙中“点击”是高频操作，如果每一次 `POST /api/ads/:id/click` 都同步写入 `data.json`，在广告被大量点击时会形成 I/O 瓶颈，不符合真实广告系统“读多写少、写入合并”的实践。
  - **实现方式**（`server/models/ads.model.ts`）：
    - 在内存中为每个广告维护 `clicks` 字段，点击时立即在内存累加，保证用户重新拉取列表时能立刻看到最新热度。
    - 额外维护一个 `clickBuffer: Map<string, number>` 与 `FLUSH_INTERVAL = 5000`，将所有点击累加到缓冲区中，由 `flushClicks` 每 5 秒批量写入 `data.json`。
    - 通过 `process.on('SIGINT'/'SIGTERM')` 注册退出钩子，在服务关闭前调用 `forceFlush`，避免缓冲中的点击数据丢失。
  - **效果**：在 5 秒内 100 次点击的场景下，从“100 次磁盘写入”降为“1 次批量写入”，极大降低了磁盘 I/O 压力，同时保持了点击统计的及时性和数据安全。

- **广告列表组件封装（前端）**
  - **业务动机**：随着广告主数量增加，广告墙可能从几十条扩展到上百、上千条，希望在不改动业务逻辑的前提下，随时可以切换到虚拟化列表以提升滚动性能。
  - **实现方式**（`client/src/components/VirtualAdGrid.tsx` + `client/src/App.tsx`）：
    - 将原本直接在 `App.tsx` 中遍历 `ads.map` 渲染卡片的逻辑，抽象为独立的 `VirtualAdGrid` 组件，由它内部负责选择渲染策略。
    - 当前实现阶段，`VirtualAdGrid` 使用原有的 `.ad-grid` + `AdCard` 渲染方式，保证视觉样式与交互不变，只是增加了一层封装。
    - 未来如果广告量级显著提升，可在不改动 `App.tsx` 的前提下，在组件内部平滑切换为 `react-virtuoso` 等虚拟化方案。
  - **效果**：在课程作业规模下保持实现简单、体验流畅的同时，通过组件边界设计为后续“高维度广告墙 + 大规模广告集”预留演进空间。

---

## 第三方库依赖

### 前端依赖

| 库名 | 版本 | 用途 | 类型 |
|------|------|------|------|
| **React** | ^18.2.0 | UI 框架，组件化开发 | 运行时 |
| **React DOM** | ^18.2.0 | React DOM 渲染 | 运行时 |
| **Axios** | ^1.6.0 | HTTP 客户端，API 请求 | 运行时 |
| **Vite** | ^4.4.5 | 构建工具，快速热更新 | 开发时 |
| **TypeScript** | ^5.3.2 | 类型检查 | 开发时 |
| **@vitejs/plugin-react** | ^4.0.3 | Vite React 插件 | 开发时 |
| **Chart.js + react-chartjs-2** | ^4.x / ^5.x | 数据可视化 | 运行时 |

### 后端依赖

| 库名 | 版本 | 用途 | 类型 |
|------|------|------|------|
| **Koa** | ^2.14.2 | Web 框架，支持 async/await | 运行时 |
| **koa-router** | ^12.0.0 | 路由管理 | 运行时 |
| **koa-body** | ^6.0.1 | 请求体解析（JSON、表单） | 运行时 |
| **@koa/cors** | ^4.0.0 | 跨域资源共享支持 | 运行时 |
| **koa-static** | ^5.0.0 | 静态文件服务 | 运行时 |
| **@koa/multer** | ^3.0.0 | 文件上传中间件 | 运行时 |
| **multer** | ^1.4.5-lts.1 | 文件上传核心处理 | 运行时 |
| **TypeScript** | ^5.3.2 | 类型检查 | 开发时 |
| **ts-node** | ^10.9.2 | TypeScript 运行时 | 开发时 |
| **ts-node-dev** | ^2.0.0 | 开发热重载 | 开发时 |

---
## 架构设计

### 项目结构

整体采用前后端分离的工程化架构，后端按“路由 → 控制器 → 服务 → 模型”的经典分层组织广告业务逻辑，前端按“页面入口 → 业务容器组件 → 纯展示组件”的方式组织 UI 与交互。

### 后端目录结构

```
server/
├── index.ts                 # 入口文件：仅负责启动服务
├── app.ts                   # 应用配置：Koa 实例和中间件注册
├── config/
│   ├── index.ts             # 配置中心：端口、路径、评分系数、LLM 配置等
│   └── formConfig.ts        # 表单配置：广告字段 JSON Schema 定义
├── routes/
│   ├── index.ts             # 路由聚合：统一注册所有路由
│   ├── ads.routes.ts        # 广告路由：CRUD 与点击统计接口、文件上传
│   ├── form.routes.ts       # 表单路由：表单配置获取接口
│   └── ai.routes.ts         # AI 路由：广告创意与投放策略建议接口
├── controllers/
│   ├── ads.controller.ts    # 广告控制器：处理广告 CRUD、点击统计、上传等请求
│   ├── form.controller.ts   # 表单控制器：表单配置相关
│   └── ai.controller.ts     # AI 控制器：LLM 广告创意与策略建议
├── services/
│   ├── ads.service.ts       # 广告服务：CRUD、排序、点击统计等核心业务逻辑
│   ├── form.service.ts      # 表单服务：表单配置和校验逻辑
│   ├── upload.service.ts    # 上传服务：文件上传处理和 URL 生成
│   ├── ai.service.ts        # AI 业务服务：组装 Prompt、校验 LLM 输出结构
│   └── llm.service.ts       # LLM 封装服务：与 OpenRouter 交互，统一调用入口
├── models/
│   └── ads.model.ts         # 数据模型：广告数据持久化层（内存 + data.json）
├── middlewares/
│   ├── errorHandler.ts      # 全局错误处理中间件
│   ├── requestLogger.ts     # 请求日志中间件
│   └── responseTime.ts      # 响应时间统计中间件
├── types/
│   ├── index.ts             # 通用类型定义（Ad、Config、AI 相关类型等）
│   └── koa.d.ts             # Koa 上下文类型扩展
├── data.json                # 数据存储文件（广告列表）
└── uploads/                 # 视频文件存储目录
```

这一层级划分保证了：  
路由只关心 URL 与方法，控制器只关心 HTTP 协议，服务集中承载广告业务规则（排序、校验、点击统计等），模型专注于数据持久化，后续如果要引入数据库或改造排序策略，只需在对应层动刀。

### 前端目录结构

```
client/
├── index.html               # HTML 入口
├── vite.config.ts           # Vite 配置与代理
├── package.json             # 依赖管理
└── src/
    ├── main.tsx             # React 入口，挂载根组件
    ├── App.tsx              # 主应用组件，承载广告列表、弹窗与数据看板
    ├── App.css              # 全局样式与设计系统（导航、卡片、弹窗、看板、AI 按钮等）
    ├── api.ts               # API 接口封装（广告 CRUD、点击统计、表单配置、上传、AI）
    ├── types/               # TypeScript 类型定义（Ad、表单配置、弹窗状态、AI 类型等）
    └── components/
        ├── AdCard.tsx       # 广告卡片组件（展示、点击入口、右上角更多操作）
        ├── AdModal.tsx      # 广告表单弹窗（Schema 驱动 + 视频上传 + AI 面板）
        ├── DeleteModal.tsx  # 删除确认弹窗
        ├── VideoModal.tsx   # 视频播放弹窗（自动播放 + 结束跳转落地页）
        └── DataDashboard.tsx# 数据可视化看板组件（出价分布、点击趋势、视频素材表现等）
```

React 端以 `App.tsx` 作为业务中枢：负责拉取广告列表、组织 CRUD 交互、承载数据看板；所有与视图相关的细节被下沉到子组件中。`api.ts` 则作为前端与后端之间的唯一数据通道，使业务代码只需要关心“拿到的就是业务实体 Ad”，而不用关心具体 HTTP 细节。

### 结合我的业务理解：

- 任务1：前后端分离版本的 mini 广告墙  
  - 后端通过“`ads.routes.ts → ads.controller.ts → ads.service.ts → ads.model.ts`”这一链路，实现了创建、编辑、删除、查询广告列表、点击次数 +1 五个核心接口，对应真实广告系统中最基础的投放单元管理。  
  - 将排序逻辑收敛在 service 层，而不是控制器或模型中，使“竞价规则”作为可独立演进的业务策略模块，贴近实际广告平台中“策略团队改规则、工程团队保接口”的协作方式。

- 任务2：增加视频上传和播放功能  
  - 将素材上传独立为 `upload.service.ts`，通过 `/api/upload` 接口统一接收文件，控制器只做流向分发，服务负责文件命名与 URL 生成，这与真实业务中“素材存储服务”解耦于“广告投放服务”的设计是一致的。  
  - 前端只持有视频 URL 列表，并在点击广告时随机选择一条进行播放，模拟真实广告系统中对多素材做 AB 测试和轮播投放的策略，同时把“素材生命周期”（上传、绑定、预览）完整走了一遍。

- 任务3：广告创建表单改为动态渲染  
  - 表单字段与校验规则被下沉到 `formConfig.ts`，由 `form.service.ts` 统一输出 Schema，前端 `AdModal.tsx` 根据 Schema 渲染组件，这种“配置驱动表单”的方式对应真实业务中“运营希望加字段、改文案但不希望频繁发版”的诉求。  
  - 将表单结构从 UI 中剥离出来，使后续支持多广告类型（信息流、搜索、品牌广告）时，只需要扩展配置而不是复制一套表单代码，体现了对广告业务高频变更和字段多样性的理解。

- LLM 与广告业务的结合（扩展能力）  
  - 通过 `ai.service.ts + llm.service.ts` 将 LLM 严格约束为“只输出 JSON 的业务助手”，前端在 `AdModal.tsx` 中将其包装为“AI 广告创意生成/AI 投放策略建议”面板，直接服务于广告主的日常投放工作。  
  - 整个链路不依赖任何特定模型，只依赖 OpenRouter 的统一接口，未来可以根据成本和效果在不同模型之间切换，这与真实商业系统中“模型可插拔、策略可调”的要求是一致的。

---

## 核心功能实现

### 1. 广告排序算法

**位置**：`server/services/ads.service.ts`

**算法公式**：
```javascript
score = pricing + (pricing × clicks × 0.42)
```

**实现逻辑**：
```javascript
function calculateScore(ad) {
    const price = parseFloat(ad.price) || 0;
    const clicks = parseInt(ad.clicks) || 0;
    return price + (price * clicks * config.AD_SCORE_FACTOR);
}
```

**设计思路**：
- **基础分**：广告出价（pricing）
- **加权分**：出价 × 点击量 × 权重系数（0.42）
- **排序**：按分数降序排列

**优势**：
- 既保证高价值广告优先展示
- 又能让有用户反馈的广告获得更好排名
- 权重系数可根据实际数据调整优化

### 2. 动态表单渲染

**位置**：`client/src/components/AdModal.tsx`

**实现流程**：

【Step 1：用户点按钮】

用户在浏览器里看到“新增广告”按钮。

前端发起请求：

```js
fetch("/api/ad/form-config")
```

【Step 2：后端收到请求，跑你的 getFormConfig】

```js
async function getFormConfig(ctx) {
    const config = formService.getAdFormConfig();  // 读取配置
    ctx.body = config;  // 返回给前端
}
```

干了两件事：

## 2.1 去拿预先写好的表单配置 JSON

```js
const config = formService.getAdFormConfig();
```

之前写死在文件里的配置：

```json
[
  { "field": "title", "name": "广告标题", "component": "Input", "validator": { "maxCount": 10 } },
  { "field": "url", "name": "落地页链接", "component": "Input", "validator": { "url": true } },
  { "field": "price", "name": "出价", "component": "Input", "validator": { "number": true } }
]
```

## 2.2 甩回去给前端

```js
ctx.body = config;
```

【Step 3：前端拿到 JSON，自动渲染表单】

前端收到 JSON 后，开始干活：

```jsx
schema.map(item => {
  return (
    <div>
      <label>{item.name}</label>
      <input name={item.field} />
    </div>
  )
})
```
所以用户瞬间看到相关内容。

【Step 4：用户开始输入表单】

用户敲字，前端一边存数据：

```js
setFormData({...})
```
【Step 5：用户点“提交”按钮】

用户点了提交，就触发前端校验：

```js
validate(formData, schema);
```

比如 schema 写了：

```json
"validator": { "number": true }
```

如果都合法，就继续。

【Step 6：前端把合法数据传给后端】

前端发：

```js
POST /api/ad/create
body: formData
```

【Step 7：后端创建成功，前端刷新广告列表】

前端收到 OK，然后刷新列表。

---

整条链总结：

**用户点按钮 → 前端问后端表单长啥样 → 后端用你那段代码把 JSON 配置扔回来 → 前端根据 JSON 自动画表单 → 用户填写 → 前端用 JSON 校验 → 提交 → 创建成功。**


```

### 3. 文件上传处理

**位置**：`server/services/upload.service.ts`

**实现流程**：

```
客户端上传文件 (multipart/form-data)
  ↓
Multer 中间件接收文件
  ↓
生成唯一文件名（时间戳 + 随机数）
  ↓
保存到 uploads/ 目录
  ↓
返回文件 URL
```

### 4. 点击统计

**位置**：`server/services/ads.service.ts`

**实现逻辑**：
```javascript
function clickAd(id) {
    const clicks = adsModel.incrementClicks(id);
    return { success: true, clicks };
}
```

### 5. 数据可视化看板实现

**位置**：前端 `client/src/components/DataDashboard.tsx`，依赖 `Chart.js` 与 `react-chartjs-2`。

在业务建模上，数据可视化围绕“出价、排序位置、点击热度、视频素材”这四个关键维度展开，用于回答以下几个典型广告业务问题：

- 不同出价区间内，广告主的竞争有多拥挤（竞价密度）。
- 出价与排序位置之间是否呈现出预期的单调关系。
- 广告在一组广告中的点击热度分布与出价之间是否匹配。
- 不同视频素材（尤其是时长维度）对点击效果是否存在显著差异。

当前实现了四个图表：

1. 广告出价分布图（柱状图）

   - 图表类型：Bar（直方图形式）
   - 横轴：出价区间（如 0–50、50–100 等）
   - 纵轴：各区间内广告数量
   - 数据来源：对 `ads.price` 做区间聚合，使用 `useMemo` 对聚合逻辑做缓存，避免重复计算。
   - 业务含义：刻画不同价格段上的广告主密度，用于观察出价集中区间与冷门区间。

2. 排名 vs 出价关系图（散点图）

   - 图表类型：Scatter
   - 横轴：广告实际出价（`price`）
   - 纵轴：广告在当前广告墙列表中的排序位置（索引加一）
   - 数据来源：后端已按综合得分排好序，前端基于排序后的列表计算排名。
   - 业务含义：判断排序逻辑是否符合“高价高位”的直觉，同时通过选中高亮支持对单个广告进行排查。

3. 广告点击热度趋势（折线图）

   - 图表类型：Line
   - 横轴：广告标题（截断后的短标题）
   - 纵轴：点击次数（`clicks`）
   - 数据来源：直接使用广告列表中的点击字段，通过 `useMemo` 生成 `datasets` 配置。
   - 业务含义：快速对比一组广告的相对热度，辅助判断哪些广告值得进一步加大预算或优化素材。

4. 视频素材表现图（散点图）

   - 图表类型：Scatter
   - 横轴：视频时长（秒）
   - 纵轴：广告点击热度（`clicks`）
   - 数据来源：目前后端未直接存储视频时长，因此在前端基于视频 URL 使用哈希函数生成一个稳定的“模拟时长”区间（5–60 秒），用于演示“素材时长与效果”的关系；同一 URL 每次生成结果一致。
   - 业务含义：模拟真实广告系统中对“视频时长与点击表现关系”的探索，为后续接入真实多媒体元数据留下空间。

此外，在“视频素材表现图”右侧增加了一张与图表卡片样式一致的白色卡片，内嵌 `Analyst.png` 插画，并在下方文案标注“新增更多图表”，用于引导后续扩展如漏斗图、素材类型分布等更复杂的分析视图。

---

## 复杂逻辑

### 1. Koa 洋葱模型设计

**位置**：`server/app.ts`

**执行顺序**：

```
请求进入 →
┌─────────────────────────────────────────────────────┐
│ errorHandler (try)                                  │
│   ┌─────────────────────────────────────────────┐   │
│   │ responseTime (start)                        │   │
│   │   ┌─────────────────────────────────────┐   │   │
│   │   │ cors                                │   │   │
│   │   │   ┌─────────────────────────────┐   │   │   │
│   │   │   │ koaBody                     │   │   │   │
│   │   │   │   ┌─────────────────────┐   │   │   │   │
│   │   │   │   │ requestLogger       │   │   │   │   │
│   │   │   │   │   ┌─────────────┐   │   │   │   │   │
│   │   │   │   │   │ serve/routes│   │   │   │   │   │
│   │   │   │   │   └─────────────┘   │   │   │   │   │
│   │   │   │   │ requestLogger (end)  │   │   │   │   │
│   │   │   │   └─────────────────────┘   │   │   │   │
│   │   │   └─────────────────────────────┘   │   │   │
│   │   └─────────────────────────────────────┘   │   │
│   │ responseTime (end)                          │   │
│   └─────────────────────────────────────────────┘   │
│ errorHandler (catch)                                │
└─────────────────────────────────────────────────────┘
响应返回 ←
```

**关键点**：
- `errorHandler` 放在最外层，捕获所有内层错误
- `responseTime` 记录整个请求的处理时间
- `requestLogger` 放在 `koaBody` 之后，才能访问请求体

### 2. 表单动态校验

**位置**：`client/src/components/AdModal.tsx`

**校验类型**：

| 校验类型 | 实现方式 | 示例 |
|---------|---------|------|
| **必填校验** | 检查 `required` 字段 | `if (required && !value) error` |
| **长度校验** | `maxLength` 规则 | `if (value.length > maxLength) error` |
| **正则校验** | `pattern` 规则 | `if (!regex.test(value)) error` |
| **数值范围** | `min/max` 规则 | `if (num < min || num > max) error` |
| **数组长度** | `maxCount` 规则 | `if (array.length > maxCount) error` |

**校验流程**：
```javascript
validateFormData(formData, formConfig)
  ↓
遍历每个字段配置
  ↓
检查必填项
  ↓
应用校验规则
  ↓
收集所有错误
  ↓
返回 { valid: boolean, errors: [] }
```

### 3. 数据持久化机制

**位置**：`server/models/ads.model.ts`

**实现策略**：

1. **内存缓存**：启动时加载到内存数组
2. **同步写入**：每次修改后立即写入文件
3. **错误处理**：读写操作都有 try-catch 保护

**数据流**：
```
启动时：data.json → 内存数组 (ads)
运行时：内存数组 ← → data.json（每次修改同步）
```

**优势**：
- 读写速度快（内存操作）
- 数据不丢失（立即持久化）

**局限性**：
- 单机部署（不支持分布式）
- 并发写入可能丢失（适合小规模使用）

---

## API 接口文档

API 端点统计
模块	端点数量	端点列表
广告管理	6 个	GET/POST /ads, PUT/DELETE /ads/:id, POST /ads/:id/click, POST /upload
表单配置	2 个	GET /form-config, POST /form-validate
AI 服务	2 个	POST /ai/creative, POST /ai/strategy
总计	10 个	

### 广告管理接口

#### 1. 获取广告列表

```http
GET /api/ads
```

**响应**：
```json
[
  {
    "id": "1",
    "title": "广告标题",
    "publisher": "发布人",
    "content": "内容",
    "url": "https://example.com",
    "price": 12.5,
    "clicks": 142,
    "videos": []
  }
]
```

**说明**：返回按排序算法排序后的广告列表

---

#### 2. 创建广告

```http
POST /api/ads
Content-Type: application/json
```

**请求体**：
```json
{
  "title": "广告标题",
  "publisher": "发布人",
  "content": "内容",
  "url": "https://example.com",
  "price": 12.5,
  "videos": ["http://localhost:3001/video1.mov"]
}
```

**响应**：
```json
{
  "id": "1765201380834",
  "title": "广告标题",
  "publisher": "发布人",
  "content": "内容",
  "url": "https://example.com",
  "price": 12.5,
  "clicks": 0,
  "videos": []
}
```

---

#### 3. 更新广告

```http
PUT /api/ads/:id
Content-Type: application/json
```

**请求体**：同创建接口

**响应**：更新后的广告对象

---

#### 4. 删除广告

```http
DELETE /api/ads/:id
```

**响应**：`204 No Content`

---

#### 5. 广告点击

```http
POST /api/ads/:id/click
```

**响应**：
```json
{
  "clicks": 143
}
```

---

### 文件上传接口

#### 上传视频

```http
POST /api/upload
Content-Type: multipart/form-data
```

**请求体**：
```
video: [文件]
```

**响应**：
```json
{
  "filename": "1765201380834-576947392.mov",
  "url": "http://localhost:3001/1765201380834-576947392.mov"
}
```

---

### 表单配置接口

#### 获取表单配置

```http
GET /api/form-config
```

**响应**：
```json
[
  {
    "field": "title",
    "name": "广告标题",
    "component": "Input",
    "placeholder": "请输入广告标题",
    "required": true,
    "validator": {
      "maxLength": 50,
      "message": "标题最多50个字符"
    }
  }
]
```

## 遇到的问题

### 1. 样式修改无法实时显示

**问题原因：**
- `vite.config.ts` 中缺少 HMR（热模块替换）优化配置
- macOS 某些情况下默认的文件系统监听不可靠
- API 代理端口配置错误（3002 → 实际是 3001）。。。。

**修复方案：**
```typescript
server: {
  hmr: { overlay: true },
  watch: { usePolling: true }  // 使用轮询确保文件变化被检测
}
```
---

### 2. 页面元素无法点击（新建广告、广告卡片等）

**问题原因：**
```css
.data-dashboard::before {
  position: absolute;   /* 绝对定位 */
  width: 200%;          /* 巨大尺寸 */
  height: 200%;
  /* 父元素没有 position: relative → 伪元素相对整个页面定位 */
  /* 没有 pointer-events: none → 拦截所有鼠标事件 */
}
```

**根本原因：** CSS 伪元素覆盖了整个页面并拦截了点击事件

**修复方案：**
```css
.data-dashboard {
  position: relative;   /* 让伪元素相对于本元素定位 */
  overflow: hidden;     /* 防止伪元素溢出 */
}
.data-dashboard::before {
  pointer-events: none; /* 不拦截鼠标事件 */
}
```

---

### 3. 视频弹窗过大

**问题原因：**
- `video-modal-box`、`video-player`、`video-close-btn`、`video-hint` 等 CSS 类**完全没有定义**
- 视频以原始尺寸显示，没有任何约束

**修复方案：** 添加完整的视频弹窗样式，限制 `max-width: 720px`、`max-height: 70vh`

---

### 4. 编辑广告弹窗无法滚动

**问题原因：**
```css
.modal-box {
  /* 缺少 max-height 限制 */
  /* 缺少 flex 布局 */
}
.modal-body {
  /* 缺少 overflow-y: auto */
}
```

**根本原因：** 弹窗没有高度限制，内容超出视口时无法滚动

**修复方案：**
```css
.modal-box {
  max-height: 90vh;
  display: flex;
  flex-direction: column;
}
.modal-body {
  overflow-y: auto;
  flex: 1;
}
```

---

### 5. 数据一致性与并发写入隐患

目前的持久化策略是“内存数组 + 每次修改同步写入 `data.json`”。在单机、低并发场景下是足够简单可靠的，但一旦有以下变化，就可能出现数据一致性问题：

- 部署多个 Node 进程或多台机器同时读写同一个 `data.json`。
- 在高频创建/编辑/点击的情况下，频繁同步写盘导致写入竞争或部分写入失败。

未来如果要支持更大的广告量或多实例部署，需要：

- 将读写抽象为一个独立的 repository 层，方便替换为真正的数据库（如 MySQL、PostgreSQL 或 Redis）。
- 在文件持久化方案下引入简单的写锁机制，或通过队列串行化写入，避免多进程同时写文件。

### 6. 排序与统计的性能瓶颈

当前实现中，每次 `GET /api/ads` 都会：

- 在服务层对所有广告重新计算 score 并排序一次。
- 在前端对整个广告列表做多次聚合（出价分布、热度趋势、视频表现等），虽然使用了 `useMemo`，但数据一变仍然需要全量重算。

在广告量较小时问题不大，但如果广告数量达到几千甚至上万：

- 服务端排序会成为 CPU 热点，需要考虑分页与增量更新（例如只对新增/变更广告重算）。
- 部分统计指标适合下沉到后端聚合，前端只负责展示，以减轻浏览器侧的计算压力。

### 7. 指标口径与业务解释风险

数据可视化看板目前只依赖“出价 + 点击数”两个维度：

- 并未统计曝光（impression），因此无法直接得出 CTR，只能用点击次数近似热度。
- 视频时长目前是前端基于 URL 哈希生成的稳定模拟值，用于演示“时长与效果”的相关性，并非真实的多媒体元数据。

在课程作业和 Demo 场景下，这样的近似是可以接受的，但如果在真实业务中直接按当前指标做投放优化，存在以下风险：

- 容易把“点击次数”误解为“转化效果”，忽略曝光基数与转化链路。
- 指标定义一旦调整（比如未来接入真实时长或曝光数据），需要在文档中明确版本和口径变化，避免分析结论混用。

---

### 9. 列表虚拟化库兼容问题（react-window）

**问题原因：**

- 在尝试为广告列表引入虚拟滚动时，我选用了 `react-window` 并写下如下导入：
  - `import { FixedSizeGrid as Grid } from 'react-window';`
- 在 Vite + ESModule 环境下运行时，浏览器报错：
  - `The requested module 'react-window' does not provide an export named 'FixedSizeGrid'`
- 同时 TypeScript 类型定义与实际导出不匹配，导致编辑器报错：
  - `Property 'FixedSizeGrid' does not exist on type ...`
- 归根结底，`react-window` 以 CommonJS 形式发布，而当前构建链路期望的是 ESM 兼容导出，导致运行时和类型层面都出现不一致。

**修复方案：**

- 考虑到当前广告墙数据量有限，优先保证系统稳定性与开发效率，选择：
  - 保留为列表虚拟化预留的封装组件 `VirtualAdGrid`，但组件内部回退为使用原有的 `.ad-grid + AdCard` 普通渲染方案。
  - 在组件注释与开发文档中明确说明：“当前使用 CSS Grid 渲染，未来可无感替换为支持 ESM 的虚拟化库（如 react-virtuoso）”。
- 同时移除对 `react-window` 的直接依赖，避免后续构建和运行时再出现兼容性问题。

**效果：**

- 修复了前端白屏问题（运行时不再报模块导出错误），保持了原有广告列表的视觉样式与交互行为。
- 通过 `VirtualAdGrid` 这一封装层，为后续真正引入虚拟滚动预留了清晰的扩展点：未来只需要在组件内部替换实现，不影响 `App.tsx` 和业务调用方。

## 教训

| 类别 | 教训 |
|------|------|
| **CSS 伪元素** | 使用 `position: absolute` 时，父元素必须有 `position: relative`；装饰性伪元素要加 `pointer-events: none` |
| **弹窗/模态框** | 始终设置 `max-height` + `overflow` 处理内容溢出 |
| **组件样式** | 确保组件使用的 CSS 类都有对应定义 |
| **开发配置** | Vite 在某些系统上需要 `usePolling` 确保文件监听可靠 |
