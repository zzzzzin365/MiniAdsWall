const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { database } = require('./helpers/mysql.cjs');

async function listen(server) { await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); return `http://127.0.0.1:${server.address().port}`; }
async function close(server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
test('hosted gateway authenticates identity, trusts server snapshot and forwards real SSE', async t => {
    const fixtures = Array.from({ length: 1001 }, (_, i) => ({ id: String(i + 1), title: 'ad', publisher: 'owner', content: 'content', url: 'https://example.com', price: 1, clicks: 0, videos: [], version: 1 }));
    const db = await database(t, fixtures); if (!db) return;
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'hosting-gateway-'));
    const seen = [];
    const backend = http.createServer(async (req, res) => {
        let body = ''; for await (const chunk of req) body += chunk;
        seen.push({ url: req.url, headers: req.headers, body: body ? JSON.parse(body) : null });
        if (req.url === '/auth/session') {
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ user_id: '11', workspace_id: '22', session_token: 'opaque-session' }));
        } else if (req.url === '/runs/1/events') {
            res.writeHead(200, { 'Content-Type': 'text/event-stream' });
            res.write('id: 1\nevent: assistant.delta\ndata: {"text":"first"}\n\n');
            setTimeout(() => res.end('id: 2\nevent: run.finished\ndata: {"status":"succeeded"}\n\n'), 150);
        } else {
            res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ id: '1', status: 'queued' }));
        }
    });
    process.env.AGENT_HOSTING_URL = await listen(backend);
    process.env.AGENT_SERVICE_TOKEN = 'service-test';
    process.env.ADS_OPERATOR_TOKEN = '';
    process.env.ADS_OPERATOR_TOKENS = JSON.stringify({ alice: 'alice-secret', bob: 'bob-secret' });
    const app = require('../dist/app').default;
    const server = http.createServer(app.callback()); const url = await listen(server);
    try {
        assert.equal((await fetch(url + '/api/agent/identity')).status, 401);
        const headers = { Authorization: 'Bearer alice-secret', 'Content-Type': 'application/json' };
        const identity = await fetch(url + '/api/agent/identity', { headers });
        assert.deepEqual(await identity.json(), { user_id: '11', workspace_id: '22' });
        assert.equal(seen[0].body.subject, 'alice');
        assert.equal(seen[0].headers.authorization, 'Bearer service-test');
        const created = await fetch(url + '/api/agent/sessions/1/runs', { method: 'POST', headers: { ...headers, 'Idempotency-Key': 'repeat-request-1234' }, body: JSON.stringify({ message: 'hello', user_id: 'bob', ads: [{ id: 'forged' }], ad_context: { version:99, items:[{id:'forged'}] } }) });
        assert.equal(created.status, 200);
        const submitted = seen.find(item => item.url === '/sessions/1/runs');
        assert.equal(submitted.headers['x-agent-session'], 'opaque-session');
        assert.ok(!submitted.body.ads.some(item => item.id === 'forged'));
        assert.equal(submitted.body.ad_context, undefined, 'new input writer remains disabled');
        assert.equal(submitted.body.ads.length, 1001, 'CP01 baseline: Koa still forwards all server ads');
        assert.deepEqual(new Set(submitted.body.ads.map(item => item.id)), new Set(fixtures.map(item => item.id)));
        // Backend above is a capture fixture; FastAPI quantity rejection is tested separately.
        const response = await fetch(url + '/api/agent/runs/1/events', { headers: { ...headers, 'Last-Event-ID': '0' } });
        const reader = response.body.getReader();
        const first = new TextDecoder().decode((await reader.read()).value);
        assert.match(first, /assistant.delta/); assert.doesNotMatch(first, /run.finished/);
        let rest = ''; while (true) { const { value, done } = await reader.read(); if (done) break; rest += new TextDecoder().decode(value); }
        assert.match(rest, /run.finished/);
        assert.equal(response.headers.get('x-accel-buffering'), 'no');
    } finally { await close(server); await close(backend); fs.rmSync(temp, { recursive: true, force: true }); await db.stop(); }
});
