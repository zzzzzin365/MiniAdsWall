// UI fixtures only: no business writes, inference or paid media submission.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const sid='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const image={id:'a'.repeat(64),asset_id:'image-toothbrush',kind:'image',subject:'历史牙刷图片',
  time_ms:null,excerpt:'白色牙刷置于桌面',source_claims:['抗菌99%（未核验）'],uncertainties:['功效未验证'],limitations:[]};
const video={...image,id:'b'.repeat(64),asset_id:'video-toothbrush',kind:'video',subject:'历史牙刷视频',
  time_ms:1500,excerpt:'视频抽样帧显示白色手柄',source_claims:[],limitations:['只理解抽样画面，未处理音频']};
const snapshot={session:{id:sid,revision:2,data:{brief_revision:1,turns:[{message:'找历史牙刷素材',job_id:'j1'}]}},
  brief:{data:{subject_description:'白色牙刷',asset_refs:[]}},jobs:[{id:'j1',status:'succeeded',result:{
    summary:'参考历史素材中的白色手柄构思桌面场景。',questions:[],source_ids:[image.id],
    media_rag:{status:'retrieved',retrieved:[image,video],citations:[image],corpus_truncated:false}}}]};
(async()=>{
  const movie=path.join(__dirname,'fixture.mp4');
  const made=spawnSync('/opt/homebrew/bin/ffmpeg',['-y','-loglevel','error','-f','lavfi','-i','color=white:s=160x90:d=3','-pix_fmt','yuv420p',movie]);
  assert.equal(made.status,0);
  const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(()=>localStorage.setItem('mini-ad-wall:operator-token','media-rag-ui-only'));
    await page.route('**/api/**',async route=>{
      assert.equal(route.request().method(),'GET');
      const p=new URL(route.request().url()).pathname;let body={};
      if(p.endsWith('/assets/image-toothbrush/content'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6yQAAAAASUVORK5CYII=','base64')});
      if(p.endsWith('/assets/video-toothbrush/content'))return route.fulfill({contentType:'video/mp4',body:fs.readFileSync(movie)});
      if(p.endsWith('/studio/brands'))body=[{id:'brand-a',name:'素材检索测试品牌'}];
      else if(p.endsWith('/studio/sessions'))body=[{id:sid,created_at:'2026-10-14T00:00:00Z'}];
      else if(p.endsWith('/studio/sessions/'+sid))body=snapshot;
      else if(p.endsWith('/capabilities'))body={enabled:true,multimodal_rag:'caption-vector-v1'};
      else if(p==='/api/ads/page')body={items:[],next_cursor:null};
      else if(p==='/api/form-config')body=[];
      return route.fulfill({json:body});
    });
    await page.goto('http://127.0.0.1:5173/?studio=1');
    await page.getByRole('button',{name:/aaaaaaaa ·/}).click();
    const sources=page.getByText('查看素材依据（引用 1 条，检索到 2 条）',{exact:true});
    await sources.click();
    await page.getByText('已引用：历史牙刷图片',{exact:true}).waitFor();
    await page.getByText('检索候选，未引用：历史牙刷视频',{exact:true}).waitFor();
    await page.getByText('素材宣称（未核验）：抗菌99%（未核验）',{exact:true}).waitFor();
    await page.waitForFunction(()=>Math.abs(document.querySelector('.studio-shell video')?.currentTime-1.5)<.1);
    await sources.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(__dirname,'desktop.png')});
    await sources.locator('..').screenshot({path:path.join(__dirname,'sources.png')});
    for(const width of [320,390,768]){
      await page.setViewportSize({width,height:900});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`overflow ${width}`);
    }
    await page.setViewportSize({width:320,height:900});
    await sources.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(__dirname,'mobile.png')});
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(__dirname,'browser.json'),JSON.stringify({status:'passed',scope:'UI fixtures; no business writes or paid inference',
      checks:['citation versus candidate','unverified claims','authorized source preview','video seek 1.5s','320/390/768 no overflow'],errors},null,2));
    console.log('media RAG browser checks passed');
  } finally {await browser.close();fs.rmSync(movie,{force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
