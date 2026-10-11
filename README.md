# 业务背景
本项目聚集真实广告系统的Ads投放链路最小闭环：“广告主创建 Campaign（广告活动）→ 平台管理 Ad Group（广告组）与 Creative（广告素材）→ 用户产生 Impression（曝光）、Click（点击）和 Conversion（转化）→ 系统回收 CTR、CVR、CPA、ROI 等效果数据并进行数据反馈”。  
传统广告墙通常只完成广告管理与数据展示：广告主看到 CTR 下降、CPA 上升或预算消耗异常后，仍需人工判断问题来自素材疲劳、受众定向、Bid（出价）、Budget（预算）还是落地页转化；再在多个页面和指标之间反复分析、调整。  
MiniAdsWall Agent 在这条最小完整业务链路上，引入广告领域 Agent，将投放数据、广告知识库和运营工具连接起来，帮助运营人员完成 Campaign 表现查询、异常归因、Creative 优化、Budget 分配、Bid 策略模拟等工作，把“看报表”升级为“得到可执行的投放建议”。

# 技术背景
<img width="4792" height="3194" alt="image" src="https://github.com/user-attachments/assets/72e1aae8-ea3b-4f7b-bdbb-995c1347af52" />

MiniAddwall + MiniAdsWall Agent 是一个一个基于 React/Koa 的全栈广告管理系统，连接至 FastAPI 多智能体 AI 后端。
前端为MiniAddwall，保留了广告增删改查、视频上传、点击追踪、排序、仪表盘以及创意生成功能。而MiniAdsWall Agent增加了代理层，包含广告代理路由、结构化广告分析工具、RAG知识检索、记忆、技能、监控与评估功能。

营销效果链路当前完成 CP00–CP04：可信曝光/点击、支付/退款、费用账本、来源核对，以及独立 MySQL outbox Worker 的最后点击归因、日聚合与显式重建。已增加 CTR/CVR/CPA/CPC/CPM/ROAS 等统一报表、不可变快照和同页效果指标（数据分析 → 投放效果），并提供单独的 eCPM/LTV 假设估算；大范围异步导出和 S1 整体验收尚未完成。默认营销入口关闭；部署需执行营销 010–013 迁移，并使用专用 Worker 数据库账号。指标口径见 [业务指标与报表](docs/marketing-reports.md)，运行边界见 [营销聚合说明](docs/marketing-aggregation.md)，阶段状态见 [整体业务向优化coding plan](docs/整体业务向优化coding%20plan.md)。本地模拟验收不代表真实营销收益或生产吞吐。

面试投屏：双击根目录 `演示.command`，或执行 `./demo.sh`，检查并启动本机演示服务后自动打开网页。`./demo.sh status` 查看状态，`./demo.sh stop` 只停止启动器创建的进程。首次配置与功能范围见 [面试演示说明](docs/interview-demo.md)。

## Repository Layout

```text
.
├── apps/
│   └── mini-ad-wall/              # React + Koa advertising product
│       ├── client/                # MiniAddwall frontend
│       └── server/                # MiniAddwall Koa backend
├── agents/                        # MiniAdsWall Agent agent orchestration
├── api/                           # MiniAdsWall Agent FastAPI app
├── core/                          # Intent recognizer and Skill loader
├── mcp/                           # Knowledge and ad tools
├── hosting/                       # Durable Agent API, Worker, MySQL and isolated tools
├── marketing/aggregation/         # Marketing attribution, daily metrics and rebuild worker
├── migrations/                    # Initial MySQL hosting schema
├── memory/                        # Conversation memory
├── monitor/                       # Runtime monitoring
├── skills/                        # Hot-loadable business rules
├── evaluation/                    # Eval harness
├── docs/                          # Architecture and demo notes
├── docker-compose.yml             # MiniAdsWall Agent services
└── requirements.txt
```

## High-Level Architecture

```mermaid
flowchart LR
  U["User / Interviewer"] --> FE["MiniAddwall React Frontend"]
  FE --> Koa["MiniAddwall Koa Backend"]
  Koa --> AdsSvc["adsAgent.service.ts"]
  AdsSvc --> Chat["MiniAdsWall Agent /chat"]
  Chat --> Intent["Intent Recognizer"]
  Intent --> Orchestrator["Agent Orchestrator"]
  Orchestrator --> AdsAgent["AdsAgent"]
  Chat --> Tools["MCP Tools: ads_summary, ad_performance_search, bid_simulation"]
  Chat --> RAG["ChromaDB RAG Knowledge"]
  Chat --> Memory["Redis + Chroma Memory"]
  AdsAgent --> Koa
  Koa --> FE
```

## Interviewer Reading Guide

- [存储、Agent 服务托管与性能设计](docs/agent-hosting-design.md)：MySQL/Redis、SSE、取消与超时、隔离执行、并发与恢复的目标设计；[实现与运行](docs/agent-hosting-implementation.md) 说明已实现能力、部署方式和未完成的生产验收。

- `apps/mini-ad-wall/server/services/adsAgent.service.ts` sends structured `ads` data to MiniAdsWall Agent instead of only a prompt summary.
- `api/main.py` accepts `ads`, calls ad tools, injects tool/RAG context, and returns `tools_used`.
- `mcp/ads_tools.py` implements `ads_summary`, `ad_performance_search`, and `bid_simulation`.
- `agents/agent_orchestrator.py` routes `ad_optimization`, `creative_generation`, and `bid_strategy` to `AdsAgent`.
- `core/intent_recognizer.py` contains multi-strategy intent recognition with ad-specific categories.
- `core/action_decision.py` turns recognition results into resumable `execute` / `clarify` / `reject` / `confirm` decisions with entity, permission, and risk checks.
- `mcp/knowledge_base.py` contains ChromaDB-backed RAG with default advertising operations documents.
- `skills/ads_optimization/SKILL.md` contains hot-loadable business rules for AdsAgent behavior.

### 广告业务数据库

Koa 广告 CRUD、操作查单和点击已迁至 MySQL 8.4，运行时不再写 JSON。新启动需设置 `ADS_MYSQL_URL`，先导入旧广告；多个 Koa 实例共享数据库。见 [迁移与多实例运行](docs/ads-mysql.md)。
