const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 // UI-only fixtures: do not issue business writes or paid generation.
 const ads=Array.from({length:13},(_,i)=>({id:`layout-${i}`,title:`广告素材 ${i+1}`,publisher:'布局验收',content:'用于检查页面排布和切换',url:'https://example.com',price:30+i*15,clicks:20+i*9,version:1,videos:[],video_count:0}));
 await page.addInitScript(()=>localStorage.setItem('mini-ad-wall:operator-token','layout-fixture-only'));
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url()); let body={};
  if(url.pathname==='/api/ads/page') body={items:ads,next_cursor:null};
  else if(url.pathname.endsWith('/studio/brands')||url.pathname.endsWith('/studio/sessions')) body=[];
  else if(url.pathname.endsWith('/capabilities')) body={};
  else if(url.pathname==='/api/form-config') body=[];
  else if(url.pathname==='/api/ai/assistant/status') body={online:false};
  await route.fulfill({json:body});
 });
 const nav=page.getByRole('navigation',{name:'模块导航'});
 const go=async name=>{await nav.getByRole('button',{name,exact:true}).click(); await page.getByRole('heading',{name,exact:true,level:1}).waitFor();};
 await page.goto('http://127.0.0.1:5173');
 await page.getByText('广告素材 1',{exact:true}).first().waitFor();
 const positions=await page.evaluate(()=>({sidebar:document.querySelector('.app-sidebar').getBoundingClientRect().right,main:document.querySelector('.app-main').getBoundingClientRect().left,background:getComputedStyle(document.body).backgroundImage}));
 assert.equal(positions.sidebar,positions.main); assert(positions.background.includes('radial-gradient'));
 await page.screenshot({path:__dirname+'/desktop-ads.png',fullPage:true});
 await page.getByRole('button',{name:'个人信息管理',exact:true}).click();
 await page.getByLabel('运营凭证',{exact:true}).waitFor();
 await page.getByRole('button',{name:'关闭个人信息管理'}).click();
 await go('已有素材管理'); assert(await page.locator('.ad-card:visible').count()>0);
 await page.reload();await page.getByRole('heading',{name:'已有素材管理',level:1}).waitFor();
 await page.screenshot({path:__dirname+'/desktop-materials.png',fullPage:true});
 await go('素材生成工作台'); await page.locator('.studio-shell').waitFor();
 await page.screenshot({path:__dirname+'/desktop-studio.png',fullPage:true});
 await page.getByPlaceholder('例如：一根白色牙刷，面向通勤人群').fill('切换页面保留此创意草稿');
 await go('数据分析');await page.locator('canvas').first().waitFor();
 await page.screenshot({path:__dirname+'/desktop-analytics.png',fullPage:true});
 await page.goBack();await page.getByRole('heading',{name:'素材生成工作台',level:1}).waitFor();
 assert.equal(await page.getByPlaceholder('例如：一根白色牙刷，面向通勤人群').inputValue(),'切换页面保留此创意草稿');
 await nav.getByRole('button',{name:'素材管理',exact:true}).click();assert.equal(await nav.getByRole('button',{name:'已有素材管理',exact:true}).isVisible(),false);
 await nav.getByRole('button',{name:'素材管理',exact:true}).click();
 await go('广告管理');await page.getByRole('button',{name:'下一页',exact:true}).click();await page.getByText('第 2 / 2 页',{exact:true}).waitFor();
 const widths=[320,390,768,1280,1440];
 for(const width of widths){
  await page.setViewportSize({width,height:900});
  for(const name of ['广告管理','已有素材管理','素材生成工作台','数据分析']){
   await go(name);await page.waitForTimeout(200);
   const scrollWidth=await page.evaluate(()=>document.documentElement.scrollWidth);
   assert(scrollWidth<=width,`${name} overflows at ${width}: ${scrollWidth}`);
  }
 }
 await page.setViewportSize({width:320,height:900});await go('素材生成工作台');
 await page.screenshot({path:__dirname+'/mobile-studio.png',fullPage:true});
 assert.deepEqual(errors,[]);
 fs.writeFileSync(__dirname+'/results.json',JSON.stringify({status:'passed',scope:'UI fixtures; no business writes or paid generation',widths,checks:['desktop left/right layout','unchanged blue gradient','sidebar account form','four module views','refresh restores module','browser back','ad pagination','studio draft survives navigation','expand/collapse material submodules','no horizontal page overflow'],errors},null,2));
 await browser.close();console.log('sidebar layout checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
