const fs=require('fs');
const path=require('path');
const {chromium}=require('/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const dir=__dirname;
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,proxy:{server:'http://127.0.0.1:7897',bypass:'127.0.0.1,localhost'}});
 const page=await browser.newPage({viewport:{width:1840,height:1460},deviceScaleFactor:1,ignoreHTTPSErrors:true});
 
 await page.route('https://embed.diagrams.net/**',async route=>{
   const u=route.request().url();
   try{const bytes=await new Promise((ok,fail)=>require('child_process').execFile('curl',['-sSL','--max-time','60',u],{encoding:null,maxBuffer:64*1024*1024},(e,b)=>e?fail(e):ok(b)));
   let ct=u.split('?')[0].endsWith('.js')?'text/javascript':u.split('?')[0].endsWith('.css')?'text/css':u.includes('.png')?'image/png':u.includes('.gif')?'image/gif':u.includes('.svg')?'image/svg+xml':u.includes('.txt')?'text/plain':'text/html';
   await route.fulfill({status:200,body:bytes,contentType:ct});}catch(e){console.log('route failed',u.slice(0,80));await route.abort();}
 });
 page.on('requestfailed',r=>console.log('requestfailed',r.url().slice(0,90),r.failure()?.errorText));
 page.on('response',r=>{if(r.status()>=400)console.log('HTTP',r.status(),r.url().slice(0,160))});
 await page.goto('http://127.0.0.1:8768/preview.html',{waitUntil:'domcontentloaded'});
 await page.evaluate(()=>{window.exports=[];window.events=[];addEventListener('message',e=>{try{const d=typeof e.data==='string'?JSON.parse(e.data):e.data;if(d?.event){window.events.push(d.event);if(d.event==='export')window.exports.push(d);}}catch{}})});
 await page.waitForFunction(()=>window.events.includes('init')||window.events.includes('load'),null,{timeout:120000});
 for(let i=1;i<=2;i++){
  const xml=fs.readFileSync(path.join(dir,`page${i}.drawio`),'utf8');
  await page.evaluate(xml=>{window.exports=[];document.getElementById('drawio').contentWindow.postMessage(JSON.stringify({action:'load',xml,autosave:0,modified:0}),'https://embed.diagrams.net');},xml);
  await page.waitForTimeout(1500);
  await page.evaluate(()=>document.getElementById('drawio').contentWindow.postMessage(JSON.stringify({action:'export',format:'svg',border:20,scale:1}),'https://embed.diagrams.net'));
  await page.waitForFunction(()=>window.exports.length>0,null,{timeout:90000});
  const data=await page.evaluate(()=>window.exports.at(-1).data);
  const svg=data.includes(';base64,')?Buffer.from(data.split(',')[1],'base64'):Buffer.from(decodeURIComponent(data.slice(data.indexOf(',')+1)));
  fs.writeFileSync(path.join(dir,`page${i}.svg`),svg);
  console.log('exported',i,svg.length);
  const preview=await browser.newPage({viewport:{width:1840,height:1460},deviceScaleFactor:1,ignoreHTTPSErrors:true});
  await preview.setContent('<html><head><style>html,body{margin:0;background:white;}svg{display:block;}</style></head><body>'+svg.toString()+'</body></html>');
  const bounds=await preview.locator('svg').boundingBox();
  await preview.setViewportSize({width:Math.ceil(bounds.width),height:Math.ceil(bounds.height)});
  await preview.screenshot({path:path.join(dir,`page${i}-pass${process.argv[2]||1}.png`),fullPage:true});
  await preview.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
