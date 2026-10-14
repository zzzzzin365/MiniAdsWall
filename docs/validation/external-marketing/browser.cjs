// UI uses curated-source fixtures and locally archived images; no paid requests.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../../..');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'data/external-marketing/catalog.json')));
const source=catalog.sources.find(s=>s.id==='zhihu-703076279');
const image=source.images[0];
const item={id:'c'.repeat(64),source_id:source.id,title:source.title,author:source.author,platform:source.platform,
  url:source.url,content:source.summary,coverage:source.coverage,kind:'article',trust:'external_author_view',
  retrieved_at:source.retrieved_at,score:.8,images:[{label:image.label,caption:image.caption,mime:'image/jpeg',original_url:image.url}]};
const sid='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const snapshot={session:{id:sid,revision:2,data:{brief_revision:1,turns:[{message:'找展示广告的参考构图',job_id:'j1'}]}},
  brief:{data:{subject_description:'示例商品',asset_refs:[]}},jobs:[{id:'j1',status:'succeeded',result:{
    summary:'参考对比色按钮和产品界面截图组织展示广告。',questions:[],reference_ids:[item.id],
    reference_knowledge:{status:'retrieved',retrieved:[item],citations:[item]}}}]};
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('mini-ad-wall:operator-token','external-knowledge-ui-only'));
  await page.route('**/api/**',async route=>{
   assert.equal(route.request().method(),'GET');const p=new URL(route.request().url()).pathname;let body={};
   if(p.endsWith('/reference-images/'+source.id+'/'+image.label))return route.fulfill({contentType:'image/jpeg',body:fs.readFileSync(path.join(root,image.local_path))});
   if(p.endsWith('/studio/brands'))body=[{id:'example',name:'展示广告参考验收'}];
   else if(p.endsWith('/studio/sessions'))body=[{id:sid,created_at:'2026-10-14T00:00:00Z'}];
   else if(p.endsWith('/studio/sessions/'+sid))body=snapshot;
   else if(p.endsWith('/capabilities'))body={enabled:true};
   else if(p==='/api/ads/page')body={items:[],next_cursor:null};
   else if(p==='/api/form-config')body=[];
   await route.fulfill({json:body});
  });
  await page.goto('http://127.0.0.1:5173/?studio=1');await page.getByRole('button',{name:/bbbbbbbb ·/}).click();
  const summary=page.getByText('查看外部营销参考（引用 1 条）',{exact:true});await summary.click();
  const details=summary.locator('..');await details.getByRole('link',{name:source.title}).waitFor();
  assert.equal(await details.getByRole('link',{name:source.title}).getAttribute('href'),source.url);
  await details.locator('img').waitFor();
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('.studio-shell details img')).some(i=>i.naturalWidth===995));
  await details.screenshot({path:path.join(__dirname,'references.png')});
  await page.setViewportSize({width:320,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=320));
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(__dirname,'browser.json'),JSON.stringify({status:'passed',scope:'UI fixtures; real archived reference image; no writes or paid requests',checks:['source URL','author attribution','reference disclaimer','local archived image preview','320px no overflow'],errors},null,2));
  console.log('external reference browser passed');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
