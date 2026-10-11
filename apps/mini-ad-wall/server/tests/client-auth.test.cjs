const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const source = readFileSync(path.join(__dirname, '../../client/src/api.ts'), 'utf8');
const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true }
}).outputText;

function load(storage, prompt, fetch) {
    let request, reject;
    const api = { interceptors: {
        request: { use(handler) { request = handler; } },
        response: { use(_resolve, handler) { reject = handler; } }
    } };
    const exports = {};
    vm.runInNewContext(js, {
        exports, require: () => ({ create: () => api }), crypto: webcrypto,
        window: { prompt }, localStorage: storage, fetch
    });
    return { request: (url = '/ads/page') => request({ url, headers: {} }), reject, client: exports };
}

function storage() {
    const values = new Map();
    return { getItem: key => values.get(key) || null,
        setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}

test('credential survives reload without another prompt', () => {
    const saved = storage();
    let prompts = 0;
    const prompt = () => { prompts++; return '  operator-secret  '; };
    assert.equal(load(saved, prompt).request().headers.Authorization, 'Bearer operator-secret');
    assert.equal(load(saved, prompt).request().headers.Authorization, 'Bearer operator-secret');
    assert.equal(prompts, 1);
});

test('401 removes saved credential; a late old 401 does not clear its replacement', async () => {
    const saved = storage();
    const answers = ['old-secret', 'new-secret'];
    const app = load(saved, () => answers.shift());
    const oldRequest = app.request();
    await assert.rejects(app.reject({ response: { status: 401 }, config: oldRequest }));
    assert.equal(app.request().headers.Authorization, 'Bearer new-secret');
    await assert.rejects(app.reject({ response: { status: 401 }, config: oldRequest }));
    assert.equal(load(saved, () => { throw new Error('unexpected prompt'); }).request().headers.Authorization, 'Bearer new-secret');
});

test('public requests and cancelled prompts do not save a credential', () => {
    const saved = storage();
    let prompts = 0;
    const app = load(saved, () => { prompts++; return null; });
    assert.equal(app.request('/ads').headers.Authorization, undefined);
    assert.equal(prompts, 0);
    assert.throws(() => app.request(), /未提供运营凭据/);
    assert.equal(saved.getItem('mini-ad-wall:operator-token'), null);
});

test('disabled storage still allows requests with an in-memory credential', () => {
    const disabled = { getItem() { throw new Error('disabled'); }, setItem() { throw new Error('disabled'); } };
    let prompts = 0;
    const app = load(disabled, () => { prompts++; return 'secret'; });
    assert.equal(app.request().headers.Authorization, 'Bearer secret');
    app.request();
    assert.equal(prompts, 1);
});

test('event stream uses saved credential and clears it on 401', async () => {
    const saved = storage();
    load(saved, () => 'secret').request();
    const app = load(saved, () => { throw new Error('unexpected prompt'); }, async (_url, options) => {
        assert.equal(options.headers.Authorization, 'Bearer secret');
        return { status: 401, ok: false };
    });
    await assert.rejects(app.client.hostedEvents('run', 0, {}, () => {}), /events:401/);
    assert.equal(saved.getItem('mini-ad-wall:operator-token'), null);
});
