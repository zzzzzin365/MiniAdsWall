const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { mkdtempSync, readFileSync, rmSync, renameSync, mkdirSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { randomUUID } = require('node:crypto');

test('business boundary, durable replay, trusted snapshots and Agent outage', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'ads-boundary-'));
    const dataFile = path.join(dir, 'ads.json');
    let captured;
    const agent = http.createServer(async (req, res) => {
        let body = ''; for await (const chunk of req) body += chunk;
        captured = { url: req.url, headers: req.headers, body: body ? JSON.parse(body) : null };
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify(req.url === '/generate/creative' ? { titles: ['generated'], texts: ['text'], scripts: ['script'], keywords: ['key'] } : { response: 'analysis', decision: 'execute', conv_id: 'test', missing_fields: [] }));
    });
    await new Promise(r => agent.listen(0, '127.0.0.1', r));
    const agentPort = agent.address().port;
    let child, base;
    async function start() {
        child = spawn(process.execPath, ['-e', "const app=require('./dist/app').default;const s=app.listen(0,'127.0.0.1',()=>console.log('TEST_PORT='+s.address().port));"], {
            cwd: path.join(__dirname, '..'),
            env: { ...process.env, ADS_DATA_FILE: dataFile, ADS_OPERATOR_TOKEN: 'test-operator', AGENT_SERVICE_TOKEN: 'test-service', ADS_AGENT_API_URL: `http://127.0.0.1:${agentPort}` },
            stdio: ['ignore', 'pipe', 'pipe']
        });
        base = await new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error('server startup timeout')), 10000);
            child.once('exit', code => { clearTimeout(timer); reject(new Error(`startup exit ${code}`)); });
            child.stdout.on('data', chunk => { const m = chunk.toString().match(/TEST_PORT=(\d+)/); if (m) { clearTimeout(timer); resolve(`http://127.0.0.1:${m[1]}`); } });
        });
    }
    async function stop() { if (child && child.exitCode === null) { const done = new Promise(r => child.once('exit', r)); child.kill(); await done; } }
    async function call(method, url, body, id, token = 'test-operator') {
        const res = await fetch(base + url, { method, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(id ? { 'Idempotency-Key': id } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
        return { status: res.status, body: res.status === 204 ? null : await res.json() };
    }
    try {
        await start();
        const ad = { title: 'test', publisher: 'operator', content: 'content', url: 'https://example.com', price: 5 };
        assert.equal((await call('GET', '/api/ads', undefined, undefined, '')).status, 200);
        assert.equal((await call('POST', '/api/ads', ad, randomUUID(), 'bad')).status, 401);
        assert.equal((await call('POST', '/api/ads', ad)).status, 400);
        assert.equal((await call('POST', '/api/ads', { ...ad, price: 101 }, randomUUID())).status, 400);
        assert.equal((await call('POST', '/api/ads', { ...ad, budget: 1000 }, randomUUID())).status, 400);
        const id = randomUUID();
        const [first, duplicate] = await Promise.all([call('POST', '/api/ads', ad, id), call('POST', '/api/ads', ad, id)]);
        assert.equal(first.status, 201); assert.deepEqual(first, duplicate);
        assert.equal((await call('POST', '/api/ads', { ...ad, price: 6 }, id)).status, 409);
        await stop(); await start();
        assert.deepEqual(await call('POST', '/api/ads', ad, id), first);
        const status = await call('GET', `/api/operations/${id}`);
        assert.equal(status.body.body.id, first.body.id);
        const edit = randomUUID();
        assert.equal((await call('PUT', `/api/ads/${first.body.id}`, { ...ad, price: 10 }, edit)).status, 200);
        const record = JSON.parse(readFileSync(dataFile)).operations[`operator:${edit}`];
        assert.equal(record.before.find(a => a.id === first.body.id).price, 5);
        assert.equal(record.after.find(a => a.id === first.body.id).price, 10);
        // A failed disk commit must roll back memory and leave the operation retryable.
        renameSync(dataFile, dataFile + '.backup');
        mkdirSync(dataFile);
        const failedWrite = randomUUID();
        assert.equal((await call('PUT', `/api/ads/${first.body.id}`, { ...ad, price: 12 }, failedWrite)).status, 500);
        assert.equal((await call('GET', '/api/ads')).body.find(a => a.id === first.body.id).price, 10);
        assert.equal((await call('GET', `/api/operations/${failedWrite}`)).status, 404);
        rmSync(dataFile, { recursive: true });
        renameSync(dataFile + '.backup', dataFile);
        assert.equal((await call('PUT', `/api/ads/${first.body.id}`, { ...ad, price: 12 }, failedWrite)).status, 200);
        await call('POST', '/api/ai/creative', { adDescription: 'test', industry: 'retail', tone: 'neutral' });
        assert.equal(captured.url, '/generate/creative');
        assert.equal(captured.headers.authorization, 'Bearer test-service');
        assert.ok(captured.headers['x-request-id']);
        await call('POST', '/api/ai/assistant/chat', { message: '分析', userId: 'admin', ads: [{ id: 'fake' }] });
        assert.equal(captured.body.user_id, 'operator');
        assert.ok(captured.body.ads.some(a => a.id === first.body.id));
        assert.ok(!captured.body.ads.some(a => a.id === 'fake'));
        await new Promise(r => agent.close(r));
        assert.equal((await call('GET', '/api/ads')).status, 200);
        assert.equal((await call('POST', `/api/ads/${first.body.id}/click`, undefined, undefined, '')).status, 200);
        const fallback = await call('POST', '/api/ai/assistant/chat', { message: '删除广告 test' });
        assert.equal(fallback.body.decision, 'reject');
        const del = randomUUID();
        assert.equal((await call('DELETE', `/api/ads/${first.body.id}`, undefined, del)).status, 204);
        assert.equal((await call('DELETE', `/api/ads/${first.body.id}`, undefined, del)).status, 204);
    } finally {
        await stop();
        agent.close();
        rmSync(dir, { recursive: true, force: true });
    }
});
