const {test}=require('node:test');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const {fixture}=require('./helpers/marketing.cjs');
async function vite(f){
    const child=spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','0'],{cwd:path.join(__dirname,'../../client'),env:{...process.env,API_PROXY_TARGET:f.a},stdio:['ignore','pipe','pipe']});
    let output='';child.stderr.on('data',d=>output+=d);
    const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('vite startup: '+output)),10000);child.once('exit',()=>{clearTimeout(timer);reject(Error(output))});child.stdout.on('data',d=>{const m=d.toString().match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0])}})});
    return{base,stop:async()=>{if(child.exitCode===null){const done=new Promise(r=>child.once('exit',r));child.kill('SIGKILL');await done}}};
}
test('CP02 browser: rendered consumer, continuous visibility, retry identity, remount and signed click',async t=>{
    let pw;try{pw=require(process.env.MARKETING_TEST_PLAYWRIGHT||'playwright')}catch{t.skip('set MARKETING_TEST_PLAYWRIGHT to installed module');return}
    const f=await fixture(t);if(!f)return;let web,browser;
    try{
        web=await vite(f);browser=await pw.chromium.launch({headless:true,...(process.env.MARKETING_TEST_CHROME?{executablePath:process.env.MARKETING_TEST_CHROME}:{})});
        const page=await browser.newPage({viewport:{width:900,height:800}}),requests=[],legacy=[];let opportunity,failedOnce=false;
        page.on('response',async r=>{if(r.url().endsWith('/api/marketing/serve')&&r.ok())opportunity=await r.json()});
        page.on('request',r=>{if(/\/api\/ads\/.*click/.test(r.url()))legacy.push(r.url())});
        await page.route('**/api/marketing/events/impressions',async route=>{requests.push(route.request().postDataJSON());if(!failedOnce){failedOnce=true;await route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"test_outage"}}'})}else await route.continue()});
        await page.goto(web.base+'/?campaign='+f.campaign);await page.getByRole('heading',{name:'测试商品'}).waitFor();await page.waitForFunction(()=>document.querySelector('article img')?.naturalWidth>0);
        await page.waitForTimeout(500);assert.equal(requests.length,0);const deadline=Date.now()+10000;while(requests.length<2&&Date.now()<deadline)await new Promise(r=>setTimeout(r,50));assert.equal(requests.length,2);assert.deepEqual(requests[0],requests[1]);assert.equal(legacy.length,0);
        const evidence=path.resolve(__dirname,'../../../../docs/validation/marketing');fs.mkdirSync(evidence,{recursive:true});await page.screenshot({path:path.join(evidence,'consumer.png'),fullPage:true});
        await page.evaluate(async d=>{const{trackImpression}=await import('/src/hooks/impressionTracker.ts');const element=document.querySelector('article');const stop=trackImpression(element,d,()=>{});stop();window.trackerStop=trackImpression(element,d,()=>{})},opportunity);
        await page.waitForTimeout(1300);assert.equal(requests.length,2);
        const [counts]=await f.db.connection.query("SELECT COUNT(*) AS n FROM marketing_events WHERE type='impression' AND validation_status='valid'");assert.equal(Number(counts[0].n),1);
        const clickResponse=page.waitForResponse(r=>r.url().includes('/api/marketing/click/'));await page.route(f.a+'/synthetic-product**',route=>route.fulfill({status:200,body:'商品落地页'}));await page.getByRole('link',{name:'查看商品'}).click({noWaitAfter:true});assert.equal((await clickResponse).status(),302);await page.waitForURL(/synthetic-product/,{waitUntil:'commit'});assert.ok(new URL(page.url()).searchParams.get('miniads_click'));
        const trackerPage=await browser.newPage({viewport:{width:900,height:800}});await trackerPage.goto(web.base+'/');await trackerPage.evaluate(()=>{document.body.innerHTML='<div style="height:1500px"></div><article id="tracked" style="height:200px">素材</article>'});
        const observed=[];trackerPage.on('request',r=>{if(r.url().endsWith('/events/impressions'))observed.push(r.postDataJSON())});
        const d=await trackerPage.evaluate(async campaign=>{const r=await fetch('/api/marketing/serve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({campaign_id:campaign})});return r.json()},f.campaign);
        await trackerPage.evaluate(async d=>{const{trackImpression}=await import('/src/hooks/impressionTracker.ts');window.trackerStop=trackImpression(document.querySelector('#tracked'),d,()=>{})},d);
        await trackerPage.waitForTimeout(1200);assert.equal(observed.length,0);
        await trackerPage.evaluate(()=>scrollTo(0,790));await trackerPage.waitForTimeout(1100);assert.equal(observed.length,0);
        await trackerPage.evaluate(()=>scrollTo(0,1500));await trackerPage.waitForTimeout(600);
        // visibilityState is simulated; intersection and timer behavior run in real Chrome.
        await trackerPage.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'))});await trackerPage.waitForTimeout(700);assert.equal(observed.length,0);
        await trackerPage.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'))});await trackerPage.waitForTimeout(600);assert.equal(observed.length,0);await trackerPage.waitForTimeout(600);assert.equal(observed.length,1);
        await trackerPage.evaluate(()=>window.trackerStop());
    }finally{await browser?.close();await web?.stop();await f.stop()}
});
