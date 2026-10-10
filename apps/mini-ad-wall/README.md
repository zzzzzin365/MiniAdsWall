# Mini Ad Wall

Mini Ad Wall is the React/Koa product frontend for the MiniAdsWall project.

It keeps the original advertising-management features:

- ad CRUD
- video upload and playback
- click tracking
- score-based ranking
- dynamic JSON-Schema-style forms
- Chart.js dashboard
- OpenRouter creative/strategy endpoints
- AI assistant panel

## MiniAdsWall Agent Integration

The Koa backend forwards assistant requests to MiniAdsWall Agent through:

```text
POST /api/ai/assistant/chat
```

The backend sends the current ad list as structured `ads` data:

```json
{
  "message": "哪些广告应该提高出价？",
  "user_id": "mini-ad-manager",
  "conv_id": "optional",
  "ads": []
}
```

MiniAdsWall Agent uses this data through `ads_summary`, `ad_performance_search`, and `bid_simulation`, then returns an AdsAgent answer.

## Run

Server:

```bash
cd server
npm install
ADS_AGENT_API_URL=http://localhost:8000 npm run dev
```

Client:

```bash
cd client
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

## Important Files

- `server/services/adsAgent.service.ts`: MiniAdsWall Agent integration
- `server/services/ads.service.ts`: asynchronous ad CRUD and ranking
- `server/models/ads.model.ts`: MySQL transactions, operation replay, version checks and atomic clicks
- `client/src/components/AIAssistantPanel.tsx`: assistant UI
- `client/src/components/DataDashboard.tsx`: dashboard

## Advertising database

Koa now requires MySQL 8.4 and `ADS_MYSQL_URL`. Import the existing JSON before starting the new server; all replicas use the same database. PUT requests must include the returned ad `version`. See [migration and multi-instance setup](../../docs/ads-mysql.md).
