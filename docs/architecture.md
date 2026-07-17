# Architecture

This repository combines two projects into one full-stack agent system.

## Product Layer: MiniAddwall

MiniAddwall is the visible product surface:

- React + Vite frontend
- Koa + TypeScript backend
- JSON-file persistence for demo data
- Video upload and static video serving
- Click tracking with buffered writes
- Dynamic form rendering from backend config
- Chart.js dashboard
- AI assistant panel

The ad system keeps its own business depth: ranking, click events, schema-driven forms, material management, and operational dashboards.

## Agent Layer: MiniAdsWall Agent

MiniAdsWall Agent is the AI backend:

- FastAPI `/chat`
- Intent recognition
- Multi-agent orchestration
- `AdsAgent`
- MCP-style tool manager
- ChromaDB RAG
- Redis/Chroma memory
- Prometheus metrics
- Evaluation harness
- Hot-loadable Skills

## Fusion Point

MiniAddwall calls:

```text
POST /api/ai/assistant/chat
```

The Koa service then calls MiniAdsWall Agent:

```text
POST http://localhost:8000/chat
```

with a structured payload:

```json
{
  "message": "哪些广告应该提高出价？",
  "user_id": "mini-ad-manager",
  "conv_id": "optional-session-id",
  "ads": [
    {
      "id": "1",
      "title": "广告标题",
      "price": 12,
      "clicks": 30,
      "videos": ["video.mp4"]
    }
  ]
}
```

MiniAdsWall Agent does not depend only on a text summary. It calls tools over the structured ad snapshot, then injects tool results and RAG results into `AdsAgent`.

## Agent Routing

Ad-related intents include:

- `ads`
- `ad_optimization`
- `creative_generation`
- `bid_strategy`

All route to `AgentType.ADS`.

Technical issues such as upload errors or API failures still route to `TechnicalAgent`. Mixed messages can trigger parallel collaboration between `TechnicalAgent` and `AdsAgent`.

## Tool Layer

`mcp/ads_tools.py` contains:

- `ads_summary`: total ads, clicks, average price, top ads, no-video ads, score formula.
- `ad_performance_search`: finds high-priority ads by title/description/effect signals.
- `bid_simulation`: simulates a small bid increase and classifies actions.

The tool results are returned in `/chat` as:

```json
{
  "tools_used": ["ads_summary", "ad_performance_search", "bid_simulation", "knowledge_search"]
}
```

## RAG Layer

`mcp/knowledge_base.py` seeds ChromaDB with ad-operation knowledge:

- ad ranking rules
- bid optimization
- material and A/B testing
- copywriting
- dashboard interpretation
- risk boundaries
