const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

function load(api, storage = new Map()) {
    api.interceptors = { request: { use() {} }, response: { use() {} } };
    const exports = {};
    const source = readFileSync(path.join(__dirname, '../../client/src/api.ts'), 'utf8');
    const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
    vm.runInNewContext(js, {
        exports, require: () => ({ create: () => api }), crypto: webcrypto, TextEncoder,
        sessionStorage: { getItem: k => storage.get(k) || null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
        window: { prompt: () => 'test' }
    });
    return { client: exports, storage };
}
const ad = { title: 'test', publisher: 'operator', content: 'text', url: 'https://example.com', price: 10 };
const missing = () => { throw { response: { status: 404 } }; };

test('lost write response is recovered by querying the recorded result', async () => {
    let sent = 0, queried = 0;
    const { client, storage } = load({ request: async () => { sent++; throw new Error('timeout'); }, get: async () => { queried++; return { data: { state: 'completed', status: 201, body: { id: 'created' } } }; } });
    assert.equal((await client.createAd(ad)).id, 'created');
    assert.equal(sent, 1); assert.equal(queried, 1); assert.equal(storage.size, 0);
});

test('unknown result survives reload; retry queries then reuses the original operation ID', async () => {
    const ids = [], order = [];
    const api = { request: async c => { order.push('write'); ids.push(c.headers['Idempotency-Key']); throw new Error('timeout'); }, get: async () => { order.push('query'); return missing(); } };
    const { client, storage } = load(api);
    await assert.rejects(client.createAd(ad), /结果待确认/);
    assert.equal(storage.size, 1);
    api.request = async c => { order.push('write'); ids.push(c.headers['Idempotency-Key']); return { data: { id: 'created' } }; };
    assert.equal((await load(api, storage).client.createAd(ad)).id, 'created');
    assert.equal(ids[0], ids[1]);
    assert.deepEqual(order, ['write', 'query', 'query', 'write']);
    assert.equal(storage.size, 0);
});

test('unavailable status service prevents blind resubmission', async () => {
    let sent = 0;
    const api = { request: async () => { sent++; throw new Error('timeout'); }, get: async () => { throw new Error('offline'); } };
    const { client, storage } = load(api);
    await assert.rejects(client.createAd(ad), /结果待确认/);
    await assert.rejects(load(api, storage).client.createAd(ad), /offline/);
    assert.equal(sent, 1); assert.equal(storage.size, 1);
});
