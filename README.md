# 业务背景
真实广告投放链路包括“广告主创建 Campaign（广告活动）→ 平台管理 Ad Group（广告组）与 Creative（广告素材）→ 用户产生 Impression（曝光）、Click（点击）和 Conversion（转化）→ 系统回收 CTR、CVR、CPA、ROI 等效果数据”。  
但传统广告墙通常只完成广告管理与数据展示：广告主看到 CTR 下降、CPA 上升或预算消耗异常后，仍需人工判断问题来自素材疲劳、受众定向、Bid（出价）、Budget（预算）还是落地页转化；再在多个页面和指标之间反复分析、调整。  
MiniAdsWall Agent 在这条最小完整业务链路上，引入广告领域 Agent，将投放数据、广告知识库和运营工具连接起来，帮助运营人员完成 Campaign 表现查询、异常归因、Creative 优化、Budget 分配、Bid 策略模拟等工作，把“看报表”升级为“得到可执行的投放建议”。
- Graceful integration: MiniAddwall falls back to local diagnosis if MiniAdsWall Agent is unavailable.

# 技术背景
<img width="1536" height="1024" alt="v3" src="https://github.com/user-attachments/assets/56d64899-4efd-4ec3-a275-9b273d46b102" />

MiniAddwall + MiniAdsWall Agent is a full-stack advertising operations demo: a React/Koa ad management system connected to a FastAPI multi-agent AI backend.

The frontend is MiniAddwall. It keeps the original ad CRUD, video upload, click tracking, ranking, dashboard, and creative-generation features. MiniAdsWall Agent adds the agent layer: AdsAgent routing, structured ad-analysis tools, RAG knowledge retrieval, memory, Skills, monitoring, and evaluation.

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

## Quick Start

### 1. Start MiniAdsWall Agent

```bash
cp .env.example .env
```

Fill at least:

```env
ANTHROPIC_API_KEY=your_key_here
API_PORT=8000
CHROMA_HOST=localhost
CHROMA_PORT=8001
ECHOMIND_SKILLS_DIR=./skills
```

`ECHOMIND_SKILLS_DIR` is kept as a backwards-compatible environment variable name for the Skill loader.

Start MiniAdsWall Agent and its dependencies:

```bash
docker compose up --build
```

Check:

```bash
curl http://localhost:8000/health
```

### 2. Start MiniAddwall Server

```bash
cd apps/mini-ad-wall/server
npm install
ADS_AGENT_API_URL=http://localhost:8000 npm run dev
```

Optional, only for MiniAddwall's standalone creative/strategy generation endpoints:

```bash
OPENROUTER_API_KEY=your_openrouter_key ADS_AGENT_API_URL=http://localhost:8000 npm run dev
```

### 3. Start MiniAddwall Frontend

```bash
cd apps/mini-ad-wall/client
npm install
npm run dev
```

Open the Vite URL, usually:

```text
http://localhost:5173
```

Click the bottom-right AI button. If MiniAdsWall Agent is running, the assistant status shows `MiniAdsWall Agent 已连接`.

## Interviewer Reading Guide

- `apps/mini-ad-wall/server/services/adsAgent.service.ts` sends structured `ads` data to MiniAdsWall Agent instead of only a prompt summary.
- `api/main.py` accepts `ads`, calls ad tools, injects tool/RAG context, and returns `tools_used`.
- `mcp/ads_tools.py` implements `ads_summary`, `ad_performance_search`, and `bid_simulation`.
- `agents/agent_orchestrator.py` routes `ad_optimization`, `creative_generation`, and `bid_strategy` to `AdsAgent`.
- `core/intent_recognizer.py` contains multi-strategy intent recognition with ad-specific categories.
- `mcp/knowledge_base.py` contains ChromaDB-backed RAG with default advertising operations documents.
- `skills/ads_optimization/SKILL.md` contains hot-loadable business rules for AdsAgent behavior.

## Demo Questions

Try these in the MiniAddwall AI assistant:

```text
分析当前广告表现，给出三个优化动作
```

```text
哪些广告应该提高出价，哪些应该先改素材？
```

```text
帮我生成下一轮 A/B 测试计划
```

More detail: see [docs/architecture.md](docs/architecture.md), [docs/api-flow.md](docs/api-flow.md), and [docs/demo-script.md](docs/demo-script.md).

## Notes

- Do not commit real `.env` files or API keys.
- Local ChromaDB and Redis data are intentionally ignored.
- `apps/mini-ad-wall/server/uploads/` is kept with a `.gitkeep`; uploaded/demo videos are local runtime assets and are not committed.
