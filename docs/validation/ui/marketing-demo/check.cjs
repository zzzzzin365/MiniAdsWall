const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];let reportRequests=0;let listFailed=false;
 const ads=[{id:'ad-one',title:'咖啡礼盒推广',publisher:'UI fixture',content:'',url:'https://example.com',price:20,clicks:1,version:1,videos:[],video_count:2},{id:'ad-two',title:'运动鞋新品',publisher:'UI fixture',content:'',url:'https://example.com',price:30,clicks:2,version:2,videos:[],video_count:1},{id:'ad-three',title:'智能手表',publisher:'UI fixture',content:'',url:'https://example.com',price:25,clicks:3,version:1,videos:[],video_count:0}];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>localStorage.setItem('mini-ad-wall:operator-token','ui-demo-only'));
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/api/ads/page'){if(listFailed)return route.fulfill({status:503,json:{error:'UI fixture: ad list unavailable'}});return route.fulfill({json:{items:url.searchParams.has('cursor')?ads.slice(2):ads.slice(0,2),next_cursor:url.searchParams.has('cursor')?null:'next'}});}
  if(url.pathname.startsWith('/api/marketing/')){reportRequests++;return route.fulfill({status:404,json:{error:'No marketing requests expected'}});}
  await route.fulfill({json:{online:false}});
 });
 await page.goto('http://127.0.0.1:5173/#data-analysis');
 await page.getByRole('button',{name:'投放效果',exact:true}).click();
 const demo=page.getByRole('region',{name:'模拟投放效果看板',exact:true});await demo.waitFor();
 const metric=key=>demo.locator(`[data-metric="${key}"] .stat-value`);
 const number=text=>Number(text.replace(/[¥,]/g,''));
 await page.getByLabel('广告',{exact:true}).getByRole('option',{name:'咖啡礼盒推广 · ad-one',exact:true}).waitFor({state:'attached'});
 assert.equal(await page.getByText('秋季牙刷推广',{exact:true}).count(),0);
 assert(await page.getByText('模拟看板',{exact:true}).isVisible());assert.equal(await page.getByRole('button',{name:'查询业务报表',exact:true}).count(),0);assert.equal(reportRequests,0);
 const compare=demo.locator('.demo-creative-comparison tbody tr');
 let impressions=0,clicks=0,orders=0,tableSpend=0,tableRevenue=0;
 for(const row of await compare.all()){const cells=row.locator('td');impressions+=number(await cells.nth(0).innerText());clicks+=number(await cells.nth(1).innerText());orders+=number(await cells.nth(3).innerText());tableSpend+=number(await cells.nth(5).innerText());tableRevenue+=number(await cells.nth(7).innerText());}
 for(const [key,value]of Object.entries({impressions,clicks,orders,spend:tableSpend,revenue:tableRevenue}))assert.equal(number(await metric(key).innerText()),value);
 await demo.getByText('查看趋势明细',{exact:true}).click();
 const trend=demo.locator('.demo-trend-data tbody tr');
 let spend=0,revenue=0;for(const row of await trend.all()){spend+=number(await row.locator('td').nth(1).innerText());revenue+=number(await row.locator('td').nth(2).innerText());}
 assert.equal(Math.round(spend*100),Math.round(tableSpend*100));assert.equal(Math.round(revenue*100),Math.round(tableRevenue*100));
 await demo.getByText('查看趋势明细',{exact:true}).click();
 await page.getByLabel('广告',{exact:true}).selectOption('ad-one');
 assert.equal(await compare.count(),2);assert.equal(await compare.filter({hasText:'运动鞋新品'}).count(),0);
 assert.equal(await page.getByLabel('素材',{exact:true}).getByRole('option').count(),3);
 const adOneImpressions=number(await metric('impressions').innerText());
 await page.getByLabel('素材',{exact:true}).selectOption('ad-one:0');assert.equal(await compare.count(),1);
 await page.getByLabel('广告',{exact:true}).selectOption('ad-two');
 assert.equal(await page.getByLabel('素材',{exact:true}).inputValue(),'all');assert.equal(await compare.count(),1);assert.match(await compare.innerText(),/运动鞋新品/);
 await page.getByLabel('广告',{exact:true}).selectOption('ad-one');assert.equal(number(await metric('impressions').innerText()),adOneImpressions);
 await page.getByLabel('统计日期').selectOption('3');assert(number(await metric('impressions').innerText())<adOneImpressions);
 await page.getByLabel('统计日期').selectOption('7');
 await page.getByRole('button',{name:'加载更多广告数据',exact:true}).click();
 await page.getByLabel('广告',{exact:true}).getByRole('option',{name:'智能手表 · ad-three',exact:true}).waitFor({state:'attached'});
 assert.equal(await page.getByLabel('广告',{exact:true}).inputValue(),'ad-one');
 await page.getByLabel('广告',{exact:true}).selectOption('all');
 await page.evaluate(()=>window.scrollTo(0,0));await page.waitForTimeout(200);
 await page.screenshot({path:__dirname+'/desktop.png',fullPage:true});
 for(const width of [320,390,768,1280,1920]){await page.setViewportSize({width,height:1000});await page.waitForTimeout(200);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);}
 await page.setViewportSize({width:390,height:900});await page.screenshot({path:__dirname+'/mobile.png',fullPage:true});
 assert.equal(reportRequests,0);
 listFailed=true;await page.reload();await page.getByRole('button',{name:'投放效果',exact:true}).click();await demo.getByText('暂无可选广告，请先新建广告或重新加载广告列表。',{exact:true}).waitFor();assert.equal(await demo.locator('[data-metric]').count(),0);assert(await page.getByLabel('广告',{exact:true}).isDisabled());
 assert.deepEqual(errors,[]);
 fs.writeFileSync(__dirname+'/results.json',JSON.stringify({status:'passed',scope:'Client simulation dashboard; ad list uses an API fixture',checks:['empty/error ad list never shows fake ads or zero KPI results','no marketing API calls for demo','six KPIs and comparison totals reconcile','daily sums reconcile with totals','real ad names and material configuration','switching ad resets material filter','load more ads preserves selection','creative and date filtering','business query entry removed','320/390/768/1280/1920 no page overflow'],errors},null,2));
 await browser.close();console.log('marketing demo checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
