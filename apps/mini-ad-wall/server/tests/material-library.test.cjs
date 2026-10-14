const {test}=require('node:test');const assert=require('node:assert/strict');const {randomUUID}=require('node:crypto');const {database}=require('./helpers/mysql.cjs');
test('material edits and deletions preserve published ad snapshots, authenticate and replay writes',async t=>{
 process.env.ADS_UPLOAD_DIR=require('node:fs').mkdtempSync('/tmp/material-upload-test-');t.after(()=>require('node:fs').rmSync(process.env.ADS_UPLOAD_DIR,{recursive:true,force:true}));
 const db=await database(t,[]);if(!db)return;const model=require('../dist/models/ads.model').default;const library=require('../dist/services/materials.service');let server;
 const op=fn=>model.executeOperation('operator',randomUUID(),'a'.repeat(64),'test',async()=>({status:200,body:await fn()})).then(r=>r.body);
 try{
 const ad=await op(()=>model.create({title:'原素材',publisher:'test',content:'原文案',url:'https://example.com',price:1,videos:[]}));
 process.env.ADS_OPERATOR_TOKEN='library-test-token';server=require('../dist/app').default.listen(0,'127.0.0.1');await new Promise(r=>server.once('listening',r));const base='http://127.0.0.1:'+server.address().port;
 const call=(path,method='GET',body,key=randomUUID())=>fetch(base+path,{method,headers:{Authorization:'Bearer library-test-token','Content-Type':'application/json','Idempotency-Key':key},...(body?{body:JSON.stringify(body)}:{})});
 assert.equal((await fetch(base+'/api/materials/'+ad.material_id,{method:'DELETE'})).status,401);
 const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/M7sAAAAASUVORK5CYII=','base64');const form=new FormData();form.append('file',new Blob([png],{type:'image/png'}),'ready.png');
 const upload=await fetch(base+'/api/materials/upload',{method:'POST',headers:{Authorization:'Bearer library-test-token'},body:form});assert.equal(upload.status,200);const uploaded=await upload.json();assert.equal(uploaded.mime,'image/png');assert(require('node:fs').existsSync(require('node:path').join(process.env.ADS_UPLOAD_DIR,uploaded.filename)));
 const badForm=new FormData();badForm.append('file',new Blob(['not a material'],{type:'text/html'}),'unsafe.html');assert.equal((await fetch(base+'/api/materials/upload',{method:'POST',headers:{Authorization:'Bearer library-test-token'},body:badForm})).status,400);
 const update={name:'新素材',content:'新文案',videos:['/example.mp4'],images:[uploaded.url]},key=randomUUID();
 const response=await call('/api/materials/'+ad.material_id,'PUT',update,key);assert.equal(response.status,200);const saved=await response.json();assert.notEqual(saved.id,ad.material_id);
 assert.deepEqual(await (await call('/api/materials/'+ad.material_id,'PUT',update,key)).json(),saved);
 assert.equal((await call('/api/materials/'+ad.material_id)).status,404);
 assert.equal((await library.listMaterials()).items.length,1);assert.equal((await library.getMaterial(saved.id)).content,'新文案');assert.deepEqual((await library.getMaterial(saved.id)).images,[uploaded.url]);assert.deepEqual((await library.listMaterials()).items[0].images,[uploaded.url]);
 const existing=(await model.getAllAds()).find(a=>a.id===ad.id);assert.equal(existing.content,'原文案');assert.equal(existing.material_id,ad.material_id);
 assert.equal((await call('/api/materials/'+saved.id,'PUT',{name:'',content:'bad',videos:[]})).status,400);
 const create=await call('/api/materials','POST',{name:'商家准备好的图片',content:'',videos:[],images:[uploaded.url]});assert.equal(create.status,201);const created=await create.json();assert.deepEqual((await library.getMaterial(created.id)).images,[uploaded.url]);await call('/api/materials/'+created.id,'DELETE');
 assert.equal((await call('/api/materials/'+saved.id,'DELETE')).status,200);assert.equal((await library.listMaterials()).items.length,0);
 assert.equal((await model.getAllAds()).length,1);assert.equal((await call('/api/materials/'+saved.id)).status,404);
 const picture=await op(()=>library.createMaterial({name:'复用图片',content:'同一图片文案',videos:[],images:[uploaded.url]}));
 const make=title=>op(()=>model.create({title,publisher:'test',content:picture.content,url:'https://example.com',price:1,videos:[],material_id:picture.id}));
 const first=await make('图片广告 A'),second=await make('图片广告 B');
 for(const route of ['/api/ads','/api/ads/page']){const body=await(await call(route)).json();const ads=Array.isArray(body)?body:body.items;for(const id of [first.id,second.id])assert.deepEqual(ads.find(ad=>ad.id===id).images,[uploaded.url]);}
 assert.deepEqual((await(await call('/api/ads/'+first.id)).json()).images,[uploaded.url]);
 assert.equal((await library.listMaterials()).items.find(m=>m.id===picture.id).usage_count,2);
 await op(()=>model.remove(first.id));assert.equal((await library.listMaterials()).items.find(m=>m.id===picture.id).usage_count,1);
 await op(()=>model.remove(second.id));await op(()=>library.changeMaterial(picture.id));
 await model.close();await model.initialize();assert.equal((await library.listMaterials()).items.length,0);
 }finally{if(server)await new Promise(r=>server.close(r));await db.stop();}
});
