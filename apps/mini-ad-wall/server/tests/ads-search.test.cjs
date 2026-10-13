const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const { database } = require('./helpers/mysql.cjs');

test('ad text search: real database, ranked pagination, literal input and browser flow', async t => {
    const db = await database(t, []); if (!db) return;
    let server, web, browser;
    try {
        process.env.ADS_OPERATOR_TOKEN = 'search-test-operator';
        process.env.ADS_RECALL_ENABLED = 'false';
        const insert = (id, title, publisher, content, price) => db.connection.execute(
            'INSERT INTO ads_business_ads (id,title,publisher,content,url,price,videos) VALUES (?,?,?,?,?,?,?)',
            [id, title, publisher, content, 'https://example.com', price, '[]']);
        // Matching ads live beyond the unfiltered first page; client-only filtering would miss them.
        for (let i = 0; i < 60; i++) await insert(`other-${i}`, `普通广告 ${i}`, '运营', '普通内容', 100);
        for (let i = 0; i < 55; i++) await insert(`match-${i}`, `Search 广告 ${i}`, '运营', '普通内容', 60 - i);
        await insert('publisher', '千川电商推广', '字节广告君', '电商内容', 3);
        await insert('body', '长文广告', '运营', '前'.repeat(850) + '数字化营销', 2);
        await insert('literal', '优惠 %_! 活动', '运营', '普通内容', 1);

        await require('../dist/services/materials.service').backfillMaterials(db.connection);
        server = http.createServer(require('../dist/app').default.callback());
        await new Promise(r => server.listen(0, '127.0.0.1', r));
        const base = `http://127.0.0.1:${server.address().port}`;
        const headers = { Authorization: 'Bearer search-test-operator' };
        const get = async params => {
            const response = await fetch(base + '/api/ads/page?' + new URLSearchParams(params), { headers });
            return { status: response.status, body: await response.json() };
        };
        await t.test('search covers all three full fields, folds case and retains ranking across pages', async () => {
            const first = await get({ q: ' search ' });
            assert.equal(first.status, 200);
            assert.equal(first.body.items.length, 50);
            assert.ok(first.body.items.every(ad => ad.title.startsWith('Search')));
            assert.ok(first.body.items.every((ad, i, all) => !i || all[i - 1].price >= ad.price));
            const second = await get({ q: 'search', cursor: first.body.next_cursor });
            assert.equal(second.status, 200);
            assert.equal(second.body.items.length, 5);
            assert.equal(second.body.next_cursor, null);
            assert.equal(new Set([...first.body.items, ...second.body.items].map(ad => ad.id)).size, 55);
            assert.deepEqual((await get({ q: '字节广告' })).body.items.map(ad => ad.id), ['publisher']);
            assert.deepEqual((await get({ q: '数字化营销' })).body.items.map(ad => ad.id), ['body']);
            assert.deepEqual((await get({ q: '千川' })).body.items.map(ad => ad.id), ['publisher']);
        });
        await t.test('wildcards remain literal, empty input resets, and cursors cannot change searches', async () => {
            assert.deepEqual((await get({ q: '%_!' })).body.items.map(ad => ad.id), ['literal']);
            assert.deepEqual((await get({ q: "' OR 1=1 --" })).body.items, []);
            assert.deepEqual((await get({ q: '不存在的广告' })).body.items, []);
            assert.deepEqual((await get({ q: '   ' })).body.items, (await get({})).body.items);
            const first = await get({ q: 'search', limit: 1 });
            assert.equal((await get({ q: '字节广告', cursor: first.body.next_cursor })).status, 400);
            assert.equal((await get({ cursor: first.body.next_cursor })).status, 400);
            const [data, signature] = first.body.next_cursor.split('.');
            const changed = data + '.' + (signature[0] === 'A' ? 'B' : 'A') + signature.slice(1);
            assert.equal((await get({ q: 'search', cursor: changed })).status, 400);
            assert.equal((await get({ q: '字'.repeat(201) })).status, 400);
            assert.equal((await fetch(base + '/api/ads/page?q=a&q=b', { headers })).status, 400);
            assert.equal((await fetch(base + '/api/ads/page?q=search')).status, 401);
        });
        await t.test('browser: submit, paginate, clear, no results, failure retry, stale responses and mobile', async st => {
            let pw; try { pw = require(process.env.MARKETING_TEST_PLAYWRIGHT || 'playwright'); }
            catch { st.skip('set MARKETING_TEST_PLAYWRIGHT'); return; }
            web = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '0'], {
                cwd: path.join(__dirname, '../../client'), env: { ...process.env, API_PROXY_TARGET: base }, stdio: ['ignore', 'pipe', 'pipe']
            });
            const url = await new Promise((resolve, reject) => {
                const timer = setTimeout(() => reject(Error('vite startup timeout')), 10000);
                web.once('exit', () => { clearTimeout(timer); reject(Error('vite exited')); });
                web.stdout.on('data', d => { const m = String(d).match(/http:\/\/127\.0\.0\.1:\d+/); if (m) { clearTimeout(timer); resolve(m[0]); } });
            });
            browser = await pw.chromium.launch({ headless: true, ...(process.env.MARKETING_TEST_CHROME ? { executablePath: process.env.MARKETING_TEST_CHROME } : {}) });
            const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
            const errors = []; page.on('pageerror', e => errors.push(e.message));
            page.on('dialog', d => d.accept('search-test-operator'));
            await page.goto(url);
            const input = page.getByRole('searchbox', { name: '搜索广告' });
            const summary = page.locator('.management-list-footer');
            const count = async n => page.waitForFunction(n => document.querySelectorAll('.management-ad-name').length === n, n);
            const search = async q => { await input.fill(q); await input.press('Enter'); };
            await count(10);
            await search('search'); await summary.getByText('已加载 50 条匹配广告', { exact: false }).waitFor();
            const next = page.getByRole('button', { name: '下一页', exact: true });
            const previous = page.getByRole('button', { name: '上一页', exact: true });
            assert.equal(await previous.isEnabled(), false);
            const firstNames = await page.locator('.management-ad-name').allTextContents();
            await page.getByRole('checkbox', { name: '选择本页全部广告', exact: true }).check();
            await page.getByText('已选中', { exact: false }).first().waitFor();
            for (let n = 2; n <= 5; n++) {
                await next.click(); await page.getByText(`第 ${n} / 5 页（可继续加载）`, { exact: true }).waitFor(); await count(10);
            }
            assert.equal(await page.getByRole('checkbox', { name: '选择本页全部广告', exact: true }).isChecked(), false);
            await next.click(); await count(5);
            await page.getByText('第 6 / 6 页', { exact: true }).waitFor();
            assert.equal(await next.isEnabled(), false);
            await previous.click(); await count(10);
            await page.getByRole('button', { name: '已有素材管理', exact: true }).click();
            await page.locator('.material-card').first().waitFor();
            assert.equal(await page.locator('.material-card').count(), 3);
            await page.getByRole('button', { name: '广告管理', exact: true }).click();
            await search('search'); await page.getByText('第 1 / 5 页（可继续加载）', { exact: true }).waitFor();
            assert.deepEqual(await page.locator('.management-ad-name').allTextContents(), firstNames);
            await page.getByRole('combobox', { name: /^素材类型/ }).selectOption('video');
            await page.getByText('没有符合筛选条件的广告', { exact: true }).waitFor();
            await page.locator('.management-toolbar').getByRole('button', { name: '重置筛选', exact: true }).click(); await count(10);
            await search('字节广告'); await count(1);
            assert.equal(await page.locator('.management-ad-name').innerText(), '千川电商推广');
            await page.getByRole('button', { name: '清空搜索', exact: true }).click(); await count(10);
            assert.equal(await input.inputValue(), '');
            await search('没有这种广告'); await page.getByText('没有找到与“没有这种广告”匹配的广告', { exact: true }).waitFor();
            assert.equal(await page.locator('.management-ad-name').count(), 0);
            await page.getByRole('button', { name: '查看全部广告' }).click(); await count(10);
            await search('   '); await count(10);
            await page.getByRole('button', {name:'数据分析',exact:true}).click();
            await page.locator('.management-data-scope').getByText('统计范围：', { exact: false }).waitFor();
            await page.getByRole('button', {name:'广告管理',exact:true}).click();

            const apiRoute = '**/api/ads/page*';
            await page.route(apiRoute, route => new URL(route.request().url()).searchParams.get('q') === '失败'
                ? route.fulfill({ status: 503, json: { error: 'test failure' } }) : route.continue());
            await search('失败'); await page.getByRole('alert').getByText('搜索失败，请重试', { exact: false }).waitFor();
            await page.unroute(apiRoute);
            await page.getByRole('button', { name: '重试', exact: true }).click();
            await page.getByText('没有找到与“失败”匹配的广告', { exact: true }).waitFor();

            // Hold a real response, switch searches, then release it to expose stale-result bugs.
            let held, ready;
            const started = new Promise(r => ready = r);
            await page.route(apiRoute, async route => {
                const q = new URL(route.request().url()).searchParams;
                if (q.get('q') === 'search' && !q.has('cursor')) {
                    const response = await route.fetch(); held = () => route.fulfill({ response }).catch(() => {}); ready();
                } else await route.continue();
            });
            await search('search'); await started;
            await search('字节广告'); await count(1);
            await held(); await page.unroute(apiRoute);
            assert.equal(await page.locator('.management-ad-name').innerText(), '千川电商推广');
            await search('search'); await count(10);
            for (let n = 2; n <= 5; n++) { await next.click(); await page.getByText(`第 ${n} / 5 页（可继续加载）`, { exact: true }).waitFor(); }
            let heldNext, nextReady;
            const nextStarted = new Promise(r => nextReady = r);
            await page.route(apiRoute, async route => {
                if (new URL(route.request().url()).searchParams.has('cursor')) {
                    const response = await route.fetch(); heldNext = () => route.fulfill({ response }).catch(() => {}); nextReady();
                } else await route.continue();
            });
            await next.click(); await nextStarted;
            await search('字节广告'); await count(1);
            await heldNext(); await page.unroute(apiRoute);
            assert.equal(await page.locator('.management-ad-name').innerText(), '千川电商推广');

            const screenshots = process.env.ADS_SEARCH_SCREENSHOT_DIR;
            await page.evaluate(() => window.scrollTo(0, 0));
            if (screenshots) { fs.mkdirSync(screenshots, { recursive: true }); await page.screenshot({ path: path.join(screenshots, 'desktop.png') }); }
            await page.setViewportSize({ width: 390, height: 844 });
            if (screenshots) await page.screenshot({ path: path.join(screenshots, 'mobile-before.png') });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
                JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth).map(el => ({ tag: el.tagName, class: el.className, right: el.getBoundingClientRect().right })).slice(0, 20))));
            await search('字'.repeat(200)); await page.locator('.management-empty').waitFor();
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            if (screenshots) await page.screenshot({ path: path.join(screenshots, 'mobile.png') });
            await input.fill(''); await count(10);
            assert.deepEqual(errors, []);
        });
    } finally {
        await browser?.close();
        if (web && web.exitCode === null) { const done = new Promise(r => web.once('exit', r)); web.kill('SIGKILL'); await done; }
        if (server) await new Promise(r => server.close(r));
        await db.stop();
    }
});
