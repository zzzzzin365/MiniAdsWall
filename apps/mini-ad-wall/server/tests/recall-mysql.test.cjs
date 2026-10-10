const {test}=require('node:test');const assert=require('node:assert/strict');const path=require('node:path');const fs=require('node:fs');const http=require('node:http');const {randomUUID}=require('node:crypto');
const {database}=require('./helpers/mysql.cjs');
const input={title:'召回测试',publisher:'test',content:'content',url:'https://example.com',price:5,videos:[]};
test('real MySQL: transactional recall, search recheck, restart and online rebuild',async t=>{
 const db=await database(t,[]);if(!db)return;let recall,koa;
 try{
  await require('../dist/scripts/migrate-recall').migrateRecall();await require('../dist/scripts/migrate-recall').migrateRecall();
  Object.assign(process.env,{ADS_RECALL_ENABLED:'true',ADS_RECALL_TOKEN:'recall-service-test-secret',ADS_RECALL_SNAPSHOT_DIR:path.join(db.root,'snapshots'),ADS_RECALL_CONSUMER_ID:'test-consumer',ADS_OPERATOR_TOKEN:'operator-test-secret'});
  const model=require('../dist/models/ads.model').default;
  const create=async(tags)=>model.executeOperation('operator',randomUUID(),'a'.repeat(64),'POST /api/ads',async()=>({status:201,body:await model.create({...input,attributes:{tags},eligibility:{enabled:true}})}));
  const blocked=await create(['a','d']),allowed=await create(['d','e']);
  assert.deepEqual(allowed.recall_receipt,allowed.body.recall_receipt);
  const [before]=await db.connection.query('SELECT SUM(committed_seq) AS n FROM ads_business_recall_partition_state');
  await assert.rejects(model.executeOperation('operator',randomUUID(),'b'.repeat(64),'POST /api/ads',async()=>{await model.create({...input,attributes:{tags:['d']},eligibility:{enabled:true}});throw Object.assign(new Error('abort'),{status:409});}));
  const [after]=await db.connection.query('SELECT SUM(committed_seq) AS n FROM ads_business_recall_partition_state');assert.equal(after[0].n,before[0].n);
  recall=await require('../dist/recall/server').startRecall(0);process.env.ADS_RECALL_URL=`http://127.0.0.1:${recall.server.address().port}`;
  koa=http.createServer(require('../dist/app').default.callback());await new Promise(r=>koa.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${koa.address().port}`;
  const headers={Authorization:'Bearer operator-test-secret','Content-Type':'application/json'};
  const request={conditions:[{field:'tags',op:'in',values:['d','e']},{field:'tags',op:'not_in',values:['a','b','c']}],limit:50,min_receipts:[allowed.recall_receipt]};
  const response=await fetch(base+'/api/ads/search',{method:'POST',headers,body:JSON.stringify(request)});const result=await response.json();assert.equal(response.status,200,JSON.stringify(result));assert.deepEqual(result.items.map(a=>a.id),[allowed.body.id]);assert.equal(result.snapshot.partitions.length,32);
  const page=await fetch(base+'/api/ads/page?limit=1',{headers});const first=await page.json();assert.equal(first.items.length,1);assert.ok(first.next_cursor);const second=await (await fetch(base+'/api/ads/page?limit=1&cursor='+encodeURIComponent(first.next_cursor),{headers})).json();assert.notEqual(first.items[0].id,second.items[0].id);
  const missing=await fetch(base+'/api/ads/search',{method:'POST',headers,body:JSON.stringify({conditions:[{field:'unknown',op:'exists'}]})});assert.equal(missing.status,400);
  const [docs]=await db.connection.execute('SELECT doc_id FROM ads_business_recall_docs WHERE ad_id=?',[allowed.body.id]);
  const removed=await model.executeOperation('operator',randomUUID(),'c'.repeat(64),'DELETE',async()=>({status:await model.remove(allowed.body.id)?204:404,body:null}));assert.ok(removed.recall_receipt);
  let deleted,deletedResult; const deadline=Date.now()+2500; do {deleted=await fetch(base+'/api/ads/search',{method:'POST',headers,body:JSON.stringify({...request,min_receipts:[removed.recall_receipt]})});deletedResult=await deleted.json();if(deleted.status===200)break;assert.equal(deletedResult.error,'index_not_caught_up');await new Promise(r=>setTimeout(r,100));}while(Date.now()<deadline);assert.equal(deleted.status,200,JSON.stringify(deletedResult));assert.deepEqual(deletedResult.items,[]);
  const [tombstone]=await db.connection.execute('SELECT deleted FROM ads_business_recall_docs WHERE doc_id=?',[docs[0].doc_id]);assert.equal(tombstone[0].deleted,1);
  await recall.close();recall=await require('../dist/recall/server').startRecall(0);process.env.ADS_RECALL_URL=`http://127.0.0.1:${recall.server.address().port}`;
  const health=await fetch(process.env.ADS_RECALL_URL+'/health',{headers:{Authorization:'Bearer recall-service-test-secret'}});assert.equal(health.status,200);
  const rebuild=await fetch(process.env.ADS_RECALL_URL+'/rebuild',{method:'POST',headers:{Authorization:'Bearer recall-service-test-secret'}});assert.equal(rebuild.status,200);
  await new Promise(r=>setTimeout(r,500));
 }finally{if(koa){koa.closeAllConnections();await new Promise(r=>koa.close(r));}if(recall)await recall.close();delete process.env.ADS_RECALL_ENABLED;await db.stop();}
});
