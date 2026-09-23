# API Flow

The endpoints below describe the current implementation. Planned asynchronous run, SSE, cancellation/resume, and paginated history APIs are specified in [存储、Agent 服务托管与性能设计](agent-hosting-design.md); the implemented hosted path and remaining validation are described in [实现与运行](agent-hosting-implementation.md). The original synchronous endpoints remain compatible.

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
  "ads": ["current ad list"],
  "confirmation_id": "optional"
}
```

If MiniAdsWall Agent is unavailable, MiniAddwall returns a local fallback only for read-only analysis. Potential write actions and pending confirmations fail closed with `reject`.

## MiniAdsWall Agent Chat Flow

`api/main.py` handles:

1. Read conversation memory.
2. Run intent/entity recognition and the four-state preflight decision.
3. Return immediately for `clarify`, `reject`, or `confirm`.
4. Only `execute` may build ad tool/RAG context and route to an Agent.
5. Write conversation memory together with decision metadata.
6. Return response metadata.

Response shape:

```json
{
  "conv_id": "...",
  "response": "...",
  "intent": "bid_strategy",
  "agent_type": "ads",
  "decision": "execute",
  "decision_reason": "intent_clear_and_safe",
  "missing_fields": [],
  "confirmation_id": null,
  "risk_level": "low",
  "action_name": null,
  "confirmed": false,
  "escalated": false,
  "latency_ms": 1234.5,
  "knowledge_used": true,
  "tools_used": ["ads_summary", "ad_performance_search", "bid_simulation", "knowledge_search"]
}
```

The four decisions are:

- `execute`: the request is clear, permitted, and safe enough to enter the Agent/tool chain.
- `clarify`: required entities are missing; the pending task is stored and resumed on the next turn.
- `reject`: the intent is unrecognized, permission is missing, confirmation is invalid, or the user cancels.
- `confirm`: a high-risk action is stored with a TTL-bound `confirmation_id`; explicit confirmation is required.

Pending clarification and confirmation state is isolated by `user_id + conv_id` and stored in Redis with an in-process fallback. Permissions come from the server-side `ACTION_PERMISSION_MAP` and are checked both when the action is requested and when a confirmation is consumed. Production authentication must also bind `user_id` to the authenticated principal.

The currently registered advertising tools analyze data and simulate bids; they do not delete ads or write real budgets. A successful confirmation only authorizes the registered execution chain and must not be reported as a real external change unless a write tool explicitly succeeds.

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

## Chat latency limits

OpenRouter chat calls use the native Chat Completions endpoint, with a text-response adapter for existing Agent callers. Empty text, HTTP-200 error bodies, and truncated output fail explicitly. Each model call has an 18-second wall-clock limit and no automatic retry. The chat handler has a 28-second total limit; Koa waits 32 seconds and the browser 35 seconds. These limits apply to chat, not creative/strategy generation.

The three exact UI quick prompts skip model-based classification, but still pass pending-state, permission, and action checks. Other inputs run classification and entity extraction concurrently (5-second model limits). Chat retrieval uses one local knowledge search (3-second limit) without remote query rewriting/reranking. A failed specialist does not retry the same provider through GeneralAgent. The UI shows elapsed waiting time, not fabricated stage progress. This remains a non-streaming API.
