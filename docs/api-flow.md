# API Flow

## Browser to MiniAddwall

The React frontend uses `apps/mini-ad-wall/client/src/api.ts`.

Important endpoints:

```text
GET    /api/ads
POST   /api/ads
PUT    /api/ads/:id
DELETE /api/ads/:id
POST   /api/ads/:id/click
POST   /api/upload
POST   /api/ai/assistant/chat
GET    /api/ai/assistant/status
```

Vite proxies `/api` to the Koa backend:

```text
http://localhost:5173/api/* -> http://localhost:3001/api/*
```

## MiniAddwall to MiniAdsWall Agent

`apps/mini-ad-wall/server/services/adsAgent.service.ts` forwards assistant requests to MiniAdsWall Agent:

```json
{
  "message": "user question",
  "user_id": "mini-ad-manager",
  "conv_id": "optional",
  "ads": ["current ad list"]
}
```

If MiniAdsWall Agent is unavailable, MiniAddwall returns a local fallback analysis, so CRUD and dashboards remain usable.

## MiniAdsWall Agent Chat Flow

`api/main.py` handles:

1. Read conversation memory.
2. Build ad tool context from `ads`.
3. Build RAG context from `knowledge_search`.
4. Run intent recognition.
5. Route to the right Agent.
6. Write conversation memory.
7. Return response metadata.

Response shape:

```json
{
  "conv_id": "...",
  "response": "...",
  "intent": "bid_strategy",
  "agent_type": "ads",
  "escalated": false,
  "latency_ms": 1234.5,
  "knowledge_used": true,
  "tools_used": ["ads_summary", "ad_performance_search", "bid_simulation", "knowledge_search"]
}
```

## Direct Smoke Test

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "哪些广告应该提高出价？",
    "user_id": "demo",
    "ads": [
      {"id":"1","title":"夏日饮品","price":12,"clicks":30,"videos":["a.mp4"]},
      {"id":"2","title":"高端耳机","price":50,"clicks":2,"videos":[]}
    ]
  }'
```
