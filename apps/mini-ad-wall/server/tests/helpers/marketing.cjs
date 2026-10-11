const { spawn } = require('node:child_process');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const { database } = require('./mysql.cjs');
const { migrateMarketingSchema } = require('../../dist/services/marketing/migrations');
const { seedMarketingScope } = require('../../dist/services/marketing/seed');
const signingKey = 'marketing-test-signing-key-32-bytes-minimum';
const sourceToken = 'marketing-source-test-token-long-enough';
const secondToken = 'marketing-second-source-token-long-enough';
const policies = {
    shop: { token: sourceToken, environment: 'synthetic', brand_ids: ['brand_a'], interfaces: ['conversions', 'spend-entries', 'spend-coverage'] },
    agency: { token: secondToken, environment: 'synthetic', brand_ids: ['brand_a'], interfaces: ['spend-entries', 'spend-coverage'] },
    wrong: { token: 'production-source-token-long-enough', environment: 'production', brand_ids: ['brand_a'], interfaces: ['conversions'] }
};
async function fixture(t) {
    const db = await database(t); if (!db) return;
    const children = [], logs = [];
    const uploads = path.join(db.root, 'uploads'); fs.mkdirSync(path.join(uploads, 'uploads'), {recursive:true});
    fs.writeFileSync(path.join(uploads,'uploads/marketing-test.svg'),'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="300"><rect width="640" height="300" fill="#004c9e"/><text x="200" y="160" fill="white" font-size="36">MiniAdsWall</text></svg>');
    async function start(extra={}) {
        const child = spawn(process.execPath, ['-e', "const s=require('./dist/app').default.listen(0,'127.0.0.1',()=>console.log('PORT='+s.address().port));"], {cwd:path.join(__dirname,'../..'), env:{...process.env,...db.env, ADS_UPLOAD_DIR:uploads, MARKETING_EVENTS_ENABLED:'true',MARKETING_ENVIRONMENT:'synthetic',MARKETING_SIGNING_KEY:signingKey,MARKETING_SOURCES:JSON.stringify(policies),ADS_OPERATOR_TOKEN:'test-operator',...extra},stdio:['ignore','pipe','pipe']});
        children.push(child);
        child.stdout.on('data',d=>logs.push(d.toString())); child.stderr.on('data',d=>logs.push(d.toString()));
        return new Promise((resolve,reject)=> {
            const timer=setTimeout(()=>reject(Error('marketing server startup failed')),10000);
            child.once('exit',()=>{clearTimeout(timer);reject(Error(logs.join('')))});
            child.stdout.on('data',d=>{const m=d.toString().match(/PORT=(\d+)/);if(m){clearTimeout(timer);resolve('http://127.0.0.1:'+m[1])}});
        });
    }
    async function call(base,method,suffix,body,options={}) {
        const response=await fetch(base+'/api/marketing'+suffix,{method,redirect:'manual',headers:{'Content-Type':'application/json',...(options.token?{Authorization:'Bearer '+options.token}:{}),...(options.key?{'Idempotency-Key':options.key}:{}),...(options.cookie?{Cookie:options.cookie}:{})},body:body===undefined?undefined:JSON.stringify(body)});
        const raw=await response.text();let value;try{value=JSON.parse(raw)}catch{value=raw}
        return {status:response.status,body:value,cookie:response.headers.get('set-cookie')?.split(';')[0],location:response.headers.get('location')};
    }
    async function stop(){for(const child of children)if(child.exitCode===null&&child.signalCode===null){const done=new Promise(r=>child.once('exit',r));child.kill('SIGKILL');await done}await db.stop()}
    try {
        await migrateMarketingSchema();
        await seedMarketingScope({brand_id:'brand_a',product_id:'product_a',principal:'operator',name:'测试品牌',environment:'synthetic',ad_ids:['1']});
        const a=await start(),b=await start(),now=Date.now();
        const op={token:'test-operator',key:randomUUID()};
        const created=await call(a,'POST','/campaigns',{brand_id:'brand_a',product_id:'product_a',name:'消费者测试活动',objective:'conversion',starts_at:new Date(now-3600000).toISOString(),ends_at:new Date(now+86400000).toISOString()},op);
        if(created.status!==201)throw Error(JSON.stringify(created));
        const campaign=created.body.id;
        const material=await call(a,'POST','/creatives',{brand_id:'brand_a',campaign_id:campaign,ad_id:'1',title:'测试商品',asset_ref:'uploads/marketing-test.svg',asset_hash:'a'.repeat(64),landing_url:a+'/synthetic-product?product=a',authorization_ref:'test-license',authorization_expires_at:new Date(now+86400000).toISOString()},{...op,key:randomUUID()});
        if(material.status!==201)throw Error(JSON.stringify(material));
        const creative=material.body.id;
        for(const [revision,status]of[[1,'pending_review'],[2,'approved']]){const r=await call(a,'POST',`/creatives/${creative}/status`,{expected_revision:revision,status},{...op,key:randomUUID()});if(r.status!==200)throw Error(JSON.stringify(r))}
        const active=await call(a,'POST',`/campaigns/${campaign}/status`,{expected_version:1,status:'active'},{...op,key:randomUUID()});if(active.status!==200)throw Error(JSON.stringify(active));
        return {db,a,b,start,call,stop,logs,campaign,creative,now,signingKey,sourceToken,secondToken};
    } catch(e){await stop();throw e}
}
module.exports={fixture,signingKey,sourceToken,secondToken,policies};
