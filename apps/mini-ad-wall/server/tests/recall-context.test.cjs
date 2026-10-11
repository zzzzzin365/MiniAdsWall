const { test } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { database } = require("./helpers/mysql.cjs");
async function listen(server) {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return `http://127.0.0.1:${server.address().port}`;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise((r) => server.close(r));
}
test("enabled context protocol bounds real database facts and rejects forged snapshots", async (t) => {
  const ads = Array.from({ length: 101 }, (_, i) => ({
    id: String(i + 1),
    title: "ad",
    publisher: "operator",
    content: "x".repeat(5000),
    url: "https://example.com",
    price: 1,
    clicks: 0,
    videos: ["a.mp4", "b.mp4", "c.mp4"],
    version: 1,
  }));
  const db = await database(t, ads);
  if (!db) return;
  let gateway, backend, captured;
  try {
    process.env.AD_CONTEXT_V1_ENABLED = "true";
    process.env.ADS_OPERATOR_TOKEN = "context-operator-secret";
    process.env.AGENT_SERVICE_TOKEN = "context-service-secret";
    backend = http.createServer(async (req, res) => {
      let raw = "";
      for await (const part of req) raw += part;
      const body = JSON.parse(raw);
      res.setHeader("Content-Type", "application/json");
      if (req.url === "/auth/session")
        res.end(
          JSON.stringify({
            user_id: "1",
            workspace_id: "2",
            session_token: "session",
          }),
        );
      else {
        captured = body;
        res.end(JSON.stringify({ id: "1", status: "queued" }));
      }
    });
    process.env.AGENT_HOSTING_URL = await listen(backend);
    process.env.ADS_AGENT_API_URL = process.env.AGENT_HOSTING_URL;
    gateway = http.createServer(require("../dist/app").default.callback());
    const base = await listen(gateway);
    const response = await fetch(base + "/api/agent/sessions/1/runs", {
      method: "POST",
      headers: {
        Authorization: "Bearer context-operator-secret",
        "Content-Type": "application/json",
        "Idempotency-Key": "context-operation-123",
      },
      body: JSON.stringify({
        message: "analyze",
        ads: [{ id: "forged" }],
        ad_context: { items: [{ id: "forged" }] },
      }),
    });
    assert.equal(response.status, 200, await response.text());
    assert.equal(captured.ad_context.version, 1);
    assert.equal(captured.ad_context.items.length, 50);
    assert.ok(Buffer.byteLength(JSON.stringify(captured.ad_context)) <= 65536);
    assert.equal(captured.ad_context.summary, null);
    assert.equal(captured.ad_context.selection.truncated, true);
    assert.ok(
      captured.ads.every((a) => a.id !== "forged" && !("content" in a)),
    );
    assert.ok(captured.ads.every((a) => a.video_count === 3));
    const sync = await fetch(base + "/api/ai/assistant/chat", {
      method: "POST",
      headers: {
        Authorization: "Bearer context-operator-secret",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message: "分析广告", ads: [{ id: "forged" }] }),
    });
    assert.equal(sync.status, 200, await sync.text());
    assert.equal(captured.ads.length, 50);
    assert.equal(captured.ad_context.version, 1);
    assert.ok(
      captured.ads.every((a) => a.video_count === 3 && a.id !== "forged"),
    );
  } finally {
    if (gateway) await close(gateway);
    if (backend) await close(backend);
    delete process.env.AD_CONTEXT_V1_ENABLED;
    await db.stop();
  }
});
