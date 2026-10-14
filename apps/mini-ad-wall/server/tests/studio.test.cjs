const {test}=require('node:test');
const assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const {fork,spawnSync}=require('node:child_process');
const {database}=require('./helpers/mysql.cjs');
process.env.MERCHANT_WORKSPACE_ENABLED='true';process.env.STUDIO_VIDEO_ENABLED='true';process.env.ADS_OPERATOR_TOKENS=JSON.stringify({operator:'studio-test-only',outsider:'outside-test-only'});
const {migrateMarketingSchema}=require('../dist/services/marketing/migrations');
const {studioSchema}=require('../dist/services/merchant/studio/migration');
const {seedMarketingScope}=require('../dist/services/marketing/seed');
const {tick}=require('../dist/services/merchant/studio/worker');
const {verifyAsset}=require('../dist/services/merchant/studio/assets');
const app=require('../dist/app').default;
const {materialize}=require('../dist/services/merchant/studio/worker');
const fixture={summary:'文字描述：白色牙刷。',questions:['价格是多少？'],concepts:[{title:'通勤牙刷',scenario:'通勤',copy:'通勤携带',script:'5秒白色牙刷特写',video_prompt:'白色牙刷静置桌面，缓慢推进，无文字',assumptions:['场景假设']}],delivery:{objective:'traffic',audience_hypotheses:['通勤人群'],test_plan:'先试投',rationale:'验证场景',missing_data:['预算'],bid:{channel:null,billing_model:'unknown',currency:'CNY',amount_minor:null,status:'insufficient_basis'}},model:'deepseek-v4-1-flash-260910',policy:'strict-two-model-v1',status:'draft',understanding:{status:'text_only',source:'merchant_text',limitations:[]}};
const textFixture = body => ({...structuredClone(fixture), source_ids:[], media_rag:{status:body.media_knowledge.sources.length?'no_match':'empty',method:'caption-vector-v1',corpus_assets:body.media_knowledge.sources.length,corpus_truncated:body.media_knowledge.truncated,retrieved:[],citations:[]}});
test('studio real MySQL HTTP lifecycle, strict routing, scope, revisions and unknown paid submissions',async t=>{
 const db=await database(t);if(!db)return;const server=app.listen(0,'127.0.0.1');await new Promise(r=>server.once('listening',r));const base='http://127.0.0.1:'+server.address().port;
 const child=fork(path.join(__dirname,'helpers/studio-http.cjs'),[],{env:{...process.env,...db.env},stdio:['ignore','ignore','ignore','ipc']});
 const second=await new Promise((resolve,reject)=>{child.once('message',m=>resolve('http://127.0.0.1:'+m.port));child.once('error',reject);child.once('exit',()=>reject(Error('second server exited')));});
 const call=async(path,body,token='studio-test-only',key=randomUUID())=>{const r=await fetch(base+'/api/merchant/v1/studio'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':key},body:body===undefined?undefined:JSON.stringify(body)});return {status:r.status,data:await r.json()};};
 try{
 await migrateMarketingSchema();await studioSchema(true);assert.equal((await studioSchema(true)).state,'applied');
 await seedMarketingScope({brand_id:'studio-brand',product_id:'studio-product',principal:'operator',name:'工作台品牌',environment:'synthetic',ad_ids:['1']});
 assert.equal((await call('/brands',undefined,'outside-test-only')).data.length,0);
 const b=await call('/briefs',{brand_id:'studio-brand',subject_type:'product',subject_description:'白色牙刷',objective:'traffic',idea:'通勤',confirmed_facts:['白色'],asset_refs:[]});assert.equal(b.status,201);
 assert.equal((await call('/briefs',{brand_id:'studio-brand',subject_type:'product',subject_description:'x',objective:'traffic',idea:'',confirmed_facts:[],asset_refs:[]},'outside-test-only')).status,403);
 const s=await call('/sessions',{brief_id:b.data.id,brief_revision:1});assert.equal(s.status,201);const sid=s.data.id;
 const key=randomUUID(),input={expected_revision:1,message:'帮我写脚本，不生成视频'};
 const results=await Promise.all([call('/sessions/'+sid+'/turns',input,'studio-test-only',key),(async()=>{const r=await fetch(second+'/api/merchant/v1/studio/sessions/'+sid+'/turns',{method:'POST',headers:{Authorization:'Bearer studio-test-only','Content-Type':'application/json','Idempotency-Key':key},body:JSON.stringify(input)});return {status:r.status,data:await r.json()};})()]);assert.equal(results[0].status,202);assert.equal(results[1].status,202);assert.equal(results[0].data.job.id,results[1].data.job.id);
 let calls=[];await tick('synthetic',async(path,body)=>{calls.push({path,body});return textFixture(body);});assert.equal(calls.length,1);assert.equal(calls[0].path,'/studio/generate');
 await assert.rejects(materialize('http://127.0.0.1/secret'));
 await assert.rejects(materialize('https://untrusted.invalid/video.mp4'));
 process.env.STUDIO_STORAGE_DIR=path.join(db.root,'studio-private');
 const imageFile=path.join(db.root,'toothbrush.png');const made=spawnSync('/opt/homebrew/bin/ffmpeg',['-loglevel','error','-f','lavfi','-i','color=white:s=16x16','-frames:v','1',imageFile]);assert.equal(made.status,0);
 const form=new FormData();form.append('file',new Blob([fs.readFileSync(imageFile)],{type:'image/png'}),'toothbrush.png');
 const upload=await fetch(base+'/api/merchant/v1/studio/assets?brand_id=studio-brand',{method:'POST',headers:{Authorization:'Bearer studio-test-only','Idempotency-Key':randomUUID()},body:form});assert.equal(upload.status,201);const a=await upload.json();
 assert.equal(a.data.analysis_job_id,undefined);const analyzed=await call('/assets/'+a.id+'/analyze',{});assert.equal(analyzed.status,202);a.data=analyzed.data.data;assert.ok(a.data.analysis_job_id);
 await tick('synthetic',async(path,body)=>{assert.equal(path,'/studio/understand');assert.equal(body.kind,'image');assert.equal(body.model,'doubao-seed-2-0-mini-260428');assert.ok(body.frames[0].data_url.startsWith('data:image/png;base64,'));return {asset_id:a.id,model:body.model,status:'succeeded',frame_count:1,subject:'测试白色图片',observations:[{description:'白色画面',frame_index:0,asset_id:a.id,time_ms:null}],source_claims:[],marketing_summary:'无法从纯色图确定商品',uncertainties:['主体不明'],limitations:[]};});
 assert.equal((await call('/jobs/'+a.data.analysis_job_id)).data.status,'succeeded');
 const ab=await call('/briefs',{brand_id:'studio-brand',subject_type:'product',subject_description:'白色牙刷',objective:'traffic',idea:'通勤',confirmed_facts:['白色'],asset_refs:[{asset_id:a.id,role:'subject_photo'}]});const ass=await call('/sessions',{brief_id:ab.data.id,brief_revision:1});await call('/sessions/'+ass.data.id+'/turns',{expected_revision:1,message:'结合识别结果给出创意'});await tick('synthetic',async(path,body)=>{assert.equal(path,'/studio/generate');assert.equal(body.asset_understandings[0].asset_id,a.id);return textFixture(body);});

 assert.equal((await fetch(base+'/api/merchant/v1/studio/assets/'+a.id+'/content',{headers:{Authorization:'Bearer outside-test-only'}})).status,403);
 assert.equal((await fetch(base+'/api/merchant/v1/studio/assets/'+a.id+'/content',{headers:{Authorization:'Bearer studio-test-only'}})).status,200);
 const snap=(await call('/sessions/'+sid)).data;assert.equal(snap.jobs[0].status,'succeeded');assert.equal((await call('/sessions/'+sid,undefined,'outside-test-only')).status,403);
 const recipe=snap.jobs[0].result.recipe_ids[0];
 assert.equal((await call('/recipes/'+recipe+'/jobs',{expected_revision:1,output_type:'image',confirm_generation:true,spec:{duration:5,resolution:'480p',ratio:'9:16'}})).status,422);
 assert.equal((await call('/recipes/'+recipe+'/jobs',{expected_revision:1,output_type:'video',confirm_generation:false,spec:{duration:5,resolution:'480p',ratio:'9:16'}})).status,422);
 const d=await call('/recipes/'+recipe+'/decisions',{expected_revision:1,decision:'accept',reason:'确认真实事实'});assert.equal(d.status,200);
 assert.equal((await call('/recipes/'+recipe+'/save-draft',{expected_revision:2,title:'通勤文案'})).status,201);
 assert.equal((await call('/recipes/'+recipe+'/decisions',{expected_revision:1,decision:'reject',reason:'旧版'})).status,409);
 const video={expected_revision:2,output_type:'video',confirm_generation:true,spec:{duration:5,resolution:'480p',ratio:'9:16'}};
 const v=await call('/recipes/'+recipe+'/jobs',video);assert.equal(v.status,202);
 const same=await call('/recipes/'+recipe+'/jobs',video);assert.equal(same.status,409);
 let submits=0;await tick('synthetic',async path=>{assert.equal(path,'/studio/video');submits++;throw Error('response lost');});assert.equal((await call('/jobs/'+v.data.id)).data.status,'submission_unknown');
 await tick('synthetic',async()=>{submits++;throw Error('must not call');});assert.equal(submits,1);
 assert.equal((await call('/recipes/'+recipe+'/jobs',video)).status,409);
 // Crash recovery is similarly conservative.
 await db.connection.execute("UPDATE merchant_studio_jobs SET status='running',lease_until=DATE_SUB(UTC_TIMESTAMP(3),INTERVAL 1 SECOND) WHERE id=?",[v.data.id]);await tick('synthetic',async()=>{throw Error('no resubmit');});assert.equal((await call('/jobs/'+v.data.id)).data.status,'submission_unknown');
 // Capability denial never consumes a provider job.
 const[n]=await db.connection.query("SELECT COUNT(*) AS n FROM merchant_studio_jobs WHERE kind='video'");assert.equal(Number(n[0].n),1);
 // Complete another session with a known provider task and exercise materialization/review/draft.
 const s2=await call('/sessions',{brief_id:b.data.id,brief_revision:1});await call('/sessions/'+s2.data.id+'/turns',{expected_revision:1,message:'新方案'});await tick('synthetic',async(path,body)=>textFixture(body));const snap2=(await call('/sessions/'+s2.data.id)).data,recipe2=snap2.jobs[0].result.recipe_ids[0];await call('/recipes/'+recipe2+'/decisions',{expected_revision:1,decision:'accept',reason:'确认'});const v2=await call('/recipes/'+recipe2+'/jobs',video);
 await tick('synthetic',async()=>({id:'provider_task_test'}));assert.equal((await call('/jobs/'+v2.data.id)).data.status,'submitted');await db.connection.execute('UPDATE merchant_studio_jobs SET lease_until=NULL,attempts=8 WHERE id=?',[v2.data.id]);
 await tick('synthetic',async()=>{throw Error('query limit must not call provider');});assert.equal((await call('/jobs/'+v2.data.id)).data.status,'failed');assert.equal((await call('/jobs/'+v2.data.id+'/retry',{confirm_retry:true})).status,200);
 await db.connection.execute('UPDATE merchant_studio_jobs SET lease_until=NULL WHERE id=?',[v2.data.id]);
 await tick('synthetic',async path=>{assert.equal(path,'/studio/video/provider_task_test');return {status:'succeeded',content:{video_url:'https://test.invalid/output.mp4'}};},async()=>({filename:'fixture.mp4',mime:'video/mp4',size:10,duration:5}));const done=(await call('/jobs/'+v2.data.id)).data;assert.equal(done.status,'succeeded');assert.ok(done.result.output_id);
 const output=done.result.output_id;assert.equal((await call('/outputs/'+output+'/save-draft',{expected_revision:1,title:'广告草稿'})).status,422);await call('/outputs/'+output+'/review',{expected_revision:1,decision:'approve',reason:'审核'});assert.equal((await call('/outputs/'+output+'/save-draft',{expected_revision:2,title:'广告草稿'})).status,201);
 assert.equal((await call('/jobs/'+v2.data.id,undefined,'outside-test-only')).status,403);
 assert.throws(()=>verifyAsset('fake.token'));
 // Actual revocation prevents reads and new processing.
 await db.connection.execute("DELETE FROM marketing_memberships WHERE principal='operator' AND brand_id='studio-brand'");assert.equal((await call('/sessions/'+sid)).status,403);
 }finally{child.kill('SIGTERM');await new Promise(r=>child.once('exit',r));await new Promise(r=>server.close(r));await db.stop();}
});
