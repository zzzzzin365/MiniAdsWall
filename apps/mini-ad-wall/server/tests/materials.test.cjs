const {test}=require('node:test');
const assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
const {database}=require('./helpers/mysql.cjs');

test('materials: real MySQL reuse, isolation, persistence and consistent API projections',async t=>{
 const db=await database(t,[]);if(!db)return;
 const model=require('../dist/models/ads.model').default;
 const {listMaterials}=require('../dist/services/materials.service');
 const input={title:'素材测试',publisher:'operator',content:'正文'.repeat(700),url:'https://example.com',price:2,videos:['/a.mp4','/b.mp4','/c.mp4']};
 const op=fn=>model.executeOperation('operator',randomUUID(),'a'.repeat(64),'test',async()=>({status:200,body:await fn()})).then(x=>x.body);
 let server;
 try{
  const [a,b]=await Promise.all([op(()=>model.create(input)),op(()=>model.create({...input,title:'另一个广告',price:5}))]);
  assert.equal(a.material_id,b.material_id);
  assert.equal((await listMaterials()).items.length,1);
  assert.equal((await listMaterials()).items[0].usage_count,2);
  const edited=await op(()=>model.update(a.id,{...input,content:'新正文',version:a.version}));
  assert.notEqual(edited.material_id,b.material_id);
  assert.equal((await model.getAllAds()).find(x=>x.id===b.id).content,input.content);
  await assert.rejects(op(()=>model.create({...input,material_id:b.material_id,content:'不匹配'})),/素材内容已修改/);
  const reused=await op(()=>model.create({...input,title:'复用',material_id:b.material_id}));
  assert.equal(reused.material_id,b.material_id);
  await assert.rejects(op(async()=>{await model.create({...input,content:'事务回滚'});throw Object.assign(Error('abort'),{status:409});}),/abort/);
  assert.equal((await listMaterials()).items.length,2);
  await op(()=>model.remove(a.id));
  assert.equal((await listMaterials()).items.length,2,'deleting an ad retains unused material');
  process.env.ADS_OPERATOR_TOKEN='material-test-token';
  server=require('../dist/app').default.listen(0,'127.0.0.1');
  await new Promise(r=>server.once('listening',r));
  const root=`http://127.0.0.1:${server.address().port}`;
  const headers={Authorization:'Bearer material-test-token'};
  assert.equal((await fetch(root+'/api/materials')).status,401);
  const page=await fetch(root+'/api/ads/page',{headers}).then(r=>r.json());
  const detail=await fetch(root+'/api/ads/'+b.id,{headers}).then(r=>r.json());
  const publicList=await fetch(root+'/api/ads').then(r=>r.json());
  for(const ad of [page.items.find(x=>x.id===b.id),detail,publicList.find(x=>x.id===b.id)]){
   assert.equal(ad.content,input.content);assert.deepEqual(ad.videos,input.videos);
   assert.equal(ad.material_id,b.material_id,JSON.stringify({keys:Object.keys(ad),id:ad.id}));assert.equal(ad.price,5);
  }
  await model.close();await model.initialize();
  assert.equal((await listMaterials()).items.find(x=>x.id===b.material_id).usage_count,2);
 }finally{if(server)await new Promise(r=>server.close(r));await db.stop();}
});
