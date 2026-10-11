const {test}=require('node:test');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const {randomUUID}=require('node:crypto');
const {fixture}=require('./helpers/marketing.cjs');
const root=path.resolve(__dirname,'../../../..');

test('embedded metric dashboard: same-page views, real report, estimates, retained snapshot, download and mobile',async t=>{
    let pw;try{pw=require(process.env.MARKETING_TEST_PLAYWRIGHT||'playwright')}catch{t.skip('set MARKETING_TEST_PLAYWRIGHT');return}
    const f=await fixture(t);if(!f)return;let browser,web;
    try{
        const display=await f.call(f.a,'POST','/serve',{campaign_id:f.campaign});
        assert.equal((await f.call(f.a,'POST','/events/impressions',{event_id:randomUUID(),impression_token:display.body.impression_token,occurred_at:new Date().toISOString()},{cookie:display.cookie})).status,201);
        const clicked=await f.call(f.a,'GET','/click/'+display.body.impression_token,undefined,{cookie:display.cookie});
        const click=new URL(clicked.location).searchParams.get('miniads_click');
        assert.equal((await f.call(f.a,'POST','/conversions',{source_event_id:randomUUID(),type:'order_paid',order_id:'browser_order',brand_id:'brand_a',product_id:'product_a',occurred_at:new Date().toISOString(),amount_minor:5000,currency:'CNY',click_token:click},{token:f.sourceToken})).status,201);
        const worker=spawn(process.env.MARKETING_TEST_PYTHON||path.join(root,'.venv/bin/python'),['-m','marketing.aggregation.worker','--environment','synthetic','--drain'],{cwd:root,env:{...process.env,MARKETING_WORKER_MYSQL_URL:f.db.env.ADS_MYSQL_URL,MARKETING_WORKER_MYSQL_SOCKET:f.db.env.ADS_MYSQL_SOCKET},stdio:['ignore','pipe','pipe']});
        let log='';worker.stdout.on('data',d=>log+=d);worker.stderr.on('data',d=>log+=d);
        assert.equal(await new Promise(r=>worker.once('exit',r)),0,log);
        web=spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','0'],{cwd:path.join(__dirname,'../../client'),env:{...process.env,API_PROXY_TARGET:f.a},stdio:['ignore','pipe','pipe']});
        const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('vite startup timeout')),10000);web.once('exit',()=>reject(Error('vite exited')));web.stdout.on('data',d=>{const m=String(d).match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0])}})});
        browser=await pw.chromium.launch({headless:true,...(process.env.MARKETING_TEST_CHROME?{executablePath:process.env.MARKETING_TEST_CHROME}:{})});
        const page=await browser.newPage({viewport:{width:1100,height:900}}),errors=[];
        page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept('test-operator'));
        await page.goto(base+'/');await page.getByRole('heading',{name:'数据分析',exact:true}).waitFor();
        assert.equal(await page.getByRole('button',{name:'广告概览',exact:true}).getAttribute('aria-pressed'),'true');
        assert.equal(await page.getByRole('link',{name:'广告效果指标',exact:true}).count(),0);
        assert.equal(await page.locator('.marketing-dashboard').count(),0);
        await page.locator('#ad-overview canvas').first().waitFor();
        assert.equal(await page.locator('#ad-overview canvas').count(),4);
        await page.getByRole('button',{name:'投放效果',exact:true}).click();
        await page.getByRole('heading',{name:'广告效果指标'}).waitFor();
        assert.equal(page.url(),base+'/');
        await page.getByLabel('品牌 ID',{exact:true}).fill('brand_a');await page.getByLabel('活动 ID',{exact:true}).fill(f.campaign);
        const response=page.waitForResponse(r=>r.url().includes('/api/marketing/reports?')&&r.status()===200);
        await page.getByRole('button',{name:'查询指标',exact:true}).click();const payload=await(await response).json();
        await page.locator('article').filter({hasText:'CTR 点击率'}).getByText('100.00%',{exact:true}).waitFor();
        await page.locator('article').filter({hasText:'CPA 转化成本'}).getByText('—',{exact:true}).waitFor();
        assert.equal(payload.metrics.cvr.value,'1.000000');
        assert.match(await page.locator('.marketing-dashboard').innerText(),/费用不完整/);
        await page.getByText('eCPM 与 LTV 估算工具',{exact:true}).click();
        for(const [label,value]of[['预估点击率（0–1）','0.1'],['预估转化率（0–1）','0.2'],['出价（分）','1000'],['平均订单净收入（分）','5000'],['月购买次数','2'],['预计客户存续（月）','12']])await page.getByLabel(label,{exact:true}).fill(value);
        await page.getByRole('button',{name:'计算估算值'}).click();await page.getByText('¥200',{exact:true}).waitFor();await page.getByText('¥1,200',{exact:true}).waitFor();
        const download=page.waitForEvent('download');await page.getByRole('button',{name:'下载此报表'}).click();const file=await download;const downloaded=JSON.parse(fs.readFileSync(await file.path(),'utf8'));assert.deepEqual(downloaded,payload);
        await page.getByLabel('历史报表 ID').fill(payload.report_id);await page.getByRole('button',{name:'查看历史快照'}).click();await page.getByText('报表 ID：'+payload.report_id,{exact:false}).waitFor();
        await page.getByRole('button',{name:'广告概览',exact:true}).click();
        assert.equal(await page.getByRole('heading',{name:'广告效果指标'}).isVisible(),false);
        assert.equal(await page.locator('#ad-overview canvas').count(),4);
        assert.ok((await page.locator('#ad-overview canvas').first().boundingBox()).width>100);
        await page.getByRole('button',{name:'投放效果',exact:true}).click();
        await page.getByText('报表 ID：'+payload.report_id,{exact:false}).waitFor();
        assert.equal(await page.getByLabel('品牌 ID',{exact:true}).inputValue(),'brand_a');
        await page.getByText('¥200',{exact:true}).waitFor();
        const evidence=path.join(root,'docs/validation/marketing/business-metrics');fs.mkdirSync(evidence,{recursive:true});await page.locator('#data-analysis').screenshot({path:path.join(evidence,'dashboard-embedded.png')});
        await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.locator('#data-analysis').screenshot({path:path.join(evidence,'mobile-embedded.png')});
        await page.goto(base+'/?marketing=1');await page.getByRole('heading',{name:'广告效果指标'}).waitFor();
        assert.equal(await page.locator('.page-title').innerText(),'广告管理');
        assert.equal(await page.getByRole('button',{name:'投放效果',exact:true}).getAttribute('aria-pressed'),'true');
        assert.deepEqual(errors,[]);
    }finally{
        await browser?.close();if(web&&web.exitCode===null){const done=new Promise(r=>web.once('exit',r));web.kill('SIGKILL');await done}await f.stop();
    }
});
