# Demo Script

Use this script when walking an interviewer through the project.

## 1. Show the Product

Open MiniAddwall:

```text
http://localhost:5173
```

Point out:

- ad cards
- create/edit/copy/delete
- video material upload
- click tracking
- ranking formula
- dashboard
- AI assistant button

## 2. Show Business Engineering

Open:

```text
apps/mini-ad-wall/server/services/ads.service.ts
apps/mini-ad-wall/server/models/ads.model.ts
apps/mini-ad-wall/client/src/components/DataDashboard.tsx
apps/mini-ad-wall/client/src/components/AdModal.tsx
```

Explain:

- controller/service/model layering
- dynamic form config
- score-based ranking
- buffered click writes
- dashboard metrics

## 3. Show Agent Integration

Open:

```text
apps/mini-ad-wall/server/services/adsAgent.service.ts
api/main.py
mcp/ads_tools.py
```

Explain:

- MiniAddwall sends structured `ads`, not only a prompt summary.
- MiniAdsWall Agent calls ad tools before the LLM answer.
- `/chat` returns `tools_used`.

## 4. Show Agent Depth

Open:

```text
core/intent_recognizer.py
agents/agent_orchestrator.py
skills/ads_optimization/SKILL.md
mcp/knowledge_base.py
```

Explain:

- ad-specific intents
- routing to AdsAgent
- Skills injection
- RAG knowledge about ranking, bidding, materials, A/B tests, and risk boundaries

## 5. Ask Good Demo Questions

In the AI assistant:

```text
分析当前广告表现，给出三个优化动作
```

```text
哪些广告应该提高出价，哪些应该先改素材？
```

```text
帮我生成下一轮 A/B 测试计划
```

```text
为什么这个高出价广告点击低？
```

The expected answer should mention current ad data, bid/material/title suggestions, and a verification plan.
