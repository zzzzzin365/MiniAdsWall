const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { database } = require('./helpers/mysql.cjs');

test('real MySQL: two Koa processes, transactions, replay and trusted Agent snapshots', async t => {
    const db = await database(t); if (!db) return;
    const children = [];
    let captured;
    const agent = http.createServer(async (req, res) => {
        let body = ''; for await (const chunk of req) body += chunk;
        captured = { url: req.url, headers: req.headers, body: body ? JSON.parse(body) : null };
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify(req.url === '/generate/creative' ? { titles: ['generated'], texts: ['text'], scripts: ['script'], keywords: ['key'] } : { response: 'analysis', decision: 'execute', conv_id: 'test', missing_fields: [] }));
    });
    await new Promise(r => agent.listen(0, '127.0.0.1', r));
    const agentPort = agent.address().port;
    async function start() {
        const child = spawn(process.execPath, ['-e', "require('./dist/models/ads.model').default.initialize().then(()=>{const app=require('./dist/app').default;const s=app.listen(0,'127.0.0.1',()=>console.log('TEST_PORT='+s.address().port));}).catch(e=>{console.error(e.message);process.exit(1)});"], {
            cwd: path.join(__dirname, '..'),
            env: { ...process.env, ...db.env, ADS_OPERATOR_TOKEN: 'test-operator', ADS_OPERATOR_TOKENS: '{"alice":"alice-token"}', AGENT_SERVICE_TOKEN: 'test-service', ADS_AGENT_API_URL: `http://127.0.0.1:${agentPort}` },
            stdio: ['ignore', 'pipe', 'pipe']
        });
        children.push(child); let output = '';
        child.stderr.on('data', chunk => { output += chunk; });
        const base = await new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error('startup timeout: ' + output)), 15000);
            child.once('exit', code => { clearTimeout(timer); reject(new Error(`startup exit ${code}: ${output}`)); });
            child.stdout.on('data', chunk => { const m = chunk.toString().match(/TEST_PORT=(\d+)/); if (m) { clearTimeout(timer); resolve(`http://127.0.0.1:${m[1]}`); } });
        });
        return { child, base };
    }
    async function stop(child) { if (child.exitCode === null && child.signalCode === null) { const done = new Promise(r => child.once('exit', r)); child.kill('SIGKILL'); await done; } }
    async function call(base, method, url, body, id, token = 'test-operator') {
        const res = await fetch(base + url, { method, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(id ? { 'Idempotency-Key': id } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
        return { status: res.status, body: res.status === 204 ? null : await res.json() };
    }
    try {
        let a = await start(); const b = await start();
        const ad = { title: 'test', publisher: 'operator', content: 'content', url: 'https://example.com', price: 5 };
        await t.test('authentication and validation still apply', async () => {
            assert.equal((await call(a.base, 'GET', '/api/ads', undefined, undefined, '')).status, 200);
            assert.equal((await call(a.base, 'POST', '/api/ads', ad, randomUUID(), 'bad')).status, 401);
            assert.equal((await call(a.base, 'POST', '/api/ads', ad)).status, 400);
            assert.equal((await call(a.base, 'POST', '/api/ads', { ...ad, price: 101 }, randomUUID())).status, 400);
            assert.equal((await call(a.base, 'POST', '/api/ads', { ...ad, budget: 1000 }, randomUUID())).status, 400);
            assert.equal((await call(a.base, 'POST', '/api/ads', { ...ad, price: 0.000000001 }, randomUUID())).status, 400);
            const tiny = await call(a.base, 'POST', '/api/ads', { ...ad, price: 0.00000001 }, randomUUID());
            assert.equal(tiny.status, 201); assert.equal(tiny.body.price, 0.00000001);
        });
        const id = randomUUID(); let first;
        await t.test('same operation across processes commits exactly one ad and audit', async () => {
            const results = await Promise.all(Array.from({ length: 12 }, (_, i) => call(i % 2 ? a.base : b.base, 'POST', '/api/ads', ad, id)));
            first = results[0]; assert.equal(first.status, 201); assert.equal(first.body.version, 1);
            for (const result of results) assert.deepEqual(result, first);
            const [counts] = await db.connection.query('SELECT (SELECT COUNT(*) FROM ads_business_ads WHERE id = ?) AS ads, (SELECT COUNT(*) FROM ads_business_operations WHERE owner = ? AND operation_key = ?) AS operations, (SELECT COUNT(*) FROM ads_business_audit WHERE owner = ? AND operation_key = ?) AS audit', [first.body.id, 'operator', id, 'operator', id]);
            assert.deepEqual([counts[0].ads, counts[0].operations, counts[0].audit], [1, 1, 1]);
            assert.equal((await call(b.base, 'POST', '/api/ads', { ...ad, price: 6 }, id)).status, 409);
            assert.equal((await call(b.base, 'GET', `/api/operations/${id}`, undefined, undefined, 'alice-token')).status, 404);
            assert.equal((await call(b.base, 'POST', '/api/ads', ad, id, 'alice-token')).status, 201);
        });
        await t.test('concurrent edits reject the stale version and preserve the winning result', async () => {
            const results = await Promise.all([call(a.base, 'PUT', `/api/ads/${first.body.id}`, { ...ad, price: 10, version: 1 }, randomUUID()), call(b.base, 'PUT', `/api/ads/${first.body.id}`, { ...ad, price: 11, version: 1 }, randomUUID())]);
            assert.deepEqual(results.map(r => r.status).sort(), [200, 409]);
            const winning = results.find(r => r.status === 200).body;
            assert.equal(winning.version, 2);
            assert.deepEqual((await call(a.base, 'GET', '/api/ads')).body.find(v => v.id === first.body.id), winning);
            assert.equal((await call(b.base, 'PUT', `/api/ads/${first.body.id}`, { ...ad, price: 12 }, randomUUID())).status, 400);
            const [audit] = await db.connection.query('SELECT before_data, after_data FROM ads_business_audit WHERE after_data->>\'$.id\' = ? AND action LIKE \'PUT %\'', [first.body.id]);
            assert.equal(audit.length, 1); assert.equal(audit[0].before_data.price, 5); assert.equal(audit[0].after_data.version, 2);
        });
        await t.test('audit failure rolls back the ad and operation, allowing a safe retry', async () => {
            const key = randomUUID(), payload = { ...ad, price: 12, version: 2 };
            await db.connection.query("CREATE TRIGGER fail_audit BEFORE INSERT ON ads_business_audit FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'injected failure'");
            try {
                assert.equal((await call(a.base, 'PUT', `/api/ads/${first.body.id}`, payload, key)).status, 503);
                assert.equal((await call(b.base, 'GET', '/api/ads')).body.find(v => v.id === first.body.id).version, 2);
                assert.equal((await call(b.base, 'GET', `/api/operations/${key}`)).status, 404);
            } finally { await db.connection.query('DROP TRIGGER fail_audit'); }
            const successful = await call(b.base, 'PUT', `/api/ads/${first.body.id}`, payload, key);
            assert.equal(successful.status, 200); assert.equal(successful.body.version, 3);
            assert.deepEqual(await call(a.base, 'PUT', `/api/ads/${first.body.id}`, payload, key), successful);
        });
        await t.test('concurrent clicks persist across a hard process restart without overwriting edits', async () => {
            const results = await Promise.all(Array.from({ length: 40 }, (_, i) => call(i % 2 ? a.base : b.base, 'POST', `/api/ads/${first.body.id}/click`, undefined, undefined, '')));
            assert.ok(results.every(r => r.status === 200));
            assert.equal(new Set(results.map(r => r.body.clicks)).size, 40);
            await stop(a.child); a = await start();
            const value = (await call(a.base, 'GET', '/api/ads')).body.find(v => v.id === first.body.id);
            assert.equal(value.clicks, 40); assert.equal(value.price, 12); assert.equal(value.version, 3);
            assert.deepEqual(await call(a.base, 'POST', '/api/ads', ad, id), first);
            assert.equal((await call(a.base, 'GET', `/api/operations/${id}`)).body.body.id, first.body.id);
        });
        await t.test('AI calls use the shared database snapshot and remain separate from business writes', async () => {
            await call(a.base, 'POST', '/api/ai/creative', { adDescription: 'test', industry: 'retail', tone: 'neutral' });
            assert.equal(captured.url, '/generate/creative'); assert.equal(captured.headers.authorization, 'Bearer test-service'); assert.ok(captured.headers['x-request-id']);
            await call(b.base, 'POST', '/api/ai/assistant/chat', { message: '分析', userId: 'admin', ads: [{ id: 'fake' }] });
            assert.equal(captured.body.user_id, 'operator');
            assert.equal(captured.body.ads.find(v => v.id === first.body.id).clicks, 40);
            assert.ok(!captured.body.ads.some(v => v.id === 'fake'));
            agent.closeAllConnections(); await new Promise(r => agent.close(r));
            const fallback = await call(a.base, 'POST', '/api/ai/assistant/chat', { message: '删除广告 test' });
            assert.equal(fallback.body.decision, 'reject');
            const del = randomUUID();
            assert.equal((await call(a.base, 'DELETE', `/api/ads/${first.body.id}`, undefined, del)).status, 204);
            assert.equal((await call(b.base, 'DELETE', `/api/ads/${first.body.id}`, undefined, del)).status, 204);
        });
    } finally {
        for (const child of children) await stop(child);
        agent.closeAllConnections(); agent.close(); await db.stop();
    }
});
