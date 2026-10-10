import http from 'http';
import path from 'path';
import os from 'os';
import { timingSafeEqual,randomUUID } from 'crypto';
import { RowDataPacket,ResultSetHeader } from 'mysql2/promise';
import { adsPool,closeAdsDatabase } from '../services/ads.database';
import { fields } from '../services/recall/repository';
import { search, failure, Condition } from './contracts';
import { Engine } from './engine';
import { poll,restore,writeSnapshot,writeDelta } from './storage';
const delay=(ms:number)=>new Promise(r=>setTimeout(r,ms));
export async function startRecall(port=Number(process.env.ADS_RECALL_PORT||3010)) {
 const secret=process.env.ADS_RECALL_TOKEN;if(!secret||secret.length<16)throw failure('recall_token_required',503);
 const directory=path.resolve(process.env.ADS_RECALL_SNAPSHOT_DIR||'recall-snapshots');
 const consumer=process.env.ADS_RECALL_CONSUMER_ID||`${os.hostname()}:${port}`,leaseConnection=await adsPool().getConnection();let epoch:string,manifest:any;
 try {
  await leaseConnection.beginTransaction();
  await leaseConnection.execute('INSERT IGNORE INTO ads_business_recall_consumers(consumer_id,generation_id,lease_until) VALUES (?,?,UTC_TIMESTAMP(3))',[consumer,randomUUID()]);
  const [rows]=await leaseConnection.execute<RowDataPacket[]>('SELECT *,lease_until>UTC_TIMESTAMP(3) AS leased FROM ads_business_recall_consumers WHERE consumer_id=? FOR UPDATE',[consumer]);
  if(Number(rows[0].leased)===1)throw failure('consumer_already_running',503);
  epoch=String(BigInt(rows[0].lease_epoch)+1n);manifest=rows[0].durable_manifest_ref;
  await leaseConnection.execute('UPDATE ads_business_recall_consumers SET lease_epoch=?,lease_until=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 15 SECOND) WHERE consumer_id=?',[epoch,consumer]);await leaseConnection.commit();
 } catch(e){await leaseConnection.rollback();throw e;}finally {leaseConnection.release();}
 let engine=new Engine(),stopping=false,rebuilding=false,checkpointing=false,lastCheckpoint=Date.now(),leaseValid=true;const handles=new Map<string,Engine>();
 const renew=async(ref?:string,generation?:string)=>{const [r]=await adsPool().execute<ResultSetHeader>('UPDATE ads_business_recall_consumers SET lease_until=DATE_ADD(UTC_TIMESTAMP(3),INTERVAL 15 SECOND),durable_manifest_ref=COALESCE(?,durable_manifest_ref),generation_id=COALESCE(?,generation_id) WHERE consumer_id=? AND lease_epoch=? AND lease_until>UTC_TIMESTAMP(3)',[ref||null,generation||null,consumer,epoch]);if(r.affectedRows!==1){leaseValid=false;throw failure('consumer_lease_lost',503);}};
 const leaseTimer=setInterval(()=>void renew().catch(()=>{leaseValid=false;engine.fatal=true;}),3000);
 try {if(manifest&&process.env.ADS_RECALL_REBUILD!=='true')await restore(engine,manifest,directory);else {const baseline=await writeSnapshot(engine,directory,true);await restore(engine,baseline,directory);await renew(baseline,engine.generation);manifest=baseline;}}
 catch(e){clearInterval(leaseTimer);await engine.close();await adsPool().execute('UPDATE ads_business_recall_consumers SET lease_until=UTC_TIMESTAMP(3) WHERE consumer_id=? AND lease_epoch=?',[consumer,epoch]);throw e;}
 async function synchronize(target:Engine){target.fatal=false;await poll(target);}
 try {await synchronize(engine);}catch(e){clearInterval(leaseTimer);await engine.close();await adsPool().execute('UPDATE ads_business_recall_consumers SET lease_until=UTC_TIMESTAMP(3) WHERE consumer_id=? AND lease_epoch=?',[consumer,epoch]);throw e;}
 async function tick(){while(!stopping){try {if(leaseValid)await synchronize(engine);engine.expire();for(const [token,target]of handles){target.expire();if(!target.hasQuery(token))handles.delete(token);}if(!checkpointing&&!rebuilding&&Date.now()-lastCheckpoint>=60000&&engine.ready()){checkpointing=true;const target=engine;void writeDelta(target,directory,manifest).then(async ref=>{await renew(ref,target.generation);manifest=ref;}).then(()=>{lastCheckpoint=Date.now();}).catch(()=>{engine.fatal=true;}).finally(()=>{checkpointing=false;});}}catch{engine.fatal=true;}await delay(200);}}
 void tick();
 let active=0;const waiting:{resolve:Function;reject:Function;timer:NodeJS.Timeout}[]=[];
 const acquire=async()=>{if(active<32){active++;return;}if(waiting.length>=64)throw failure('recall_overloaded',429);await new Promise((resolve,reject)=>{const item={resolve,reject,timer:setTimeout(()=>{const i=waiting.indexOf(item);if(i>=0)waiting.splice(i,1);reject(failure('recall_overloaded',429));},100)};waiting.push(item);});};
 const releaseSlot=()=>{const next=waiting.shift();if(next){clearTimeout(next.timer);next.resolve();}else active--;};
 const server=http.createServer(async(req,res)=>{
  let queryToken:string|undefined,slot=false;
  try {
   const supplied=Buffer.from(req.headers.authorization||''),expected=Buffer.from(`Bearer ${secret}`);
   if(supplied.length!==expected.length||!timingSafeEqual(supplied,expected))throw failure('authentication_required',401);
   if(req.method==='GET'&&req.url==='/health'){if(!leaseValid||!engine.ready())throw failure('index_unavailable',503);res.end(JSON.stringify({status:'ready',generation_id:engine.generation,sequences:engine.sequences,pending_age_ms:engine.pendingAge,rss:process.memoryUsage().rss}));return;}
   await acquire();slot=true;req.setEncoding('utf8');
   if(process.memoryUsage().rss>28*1024**3)throw failure('recall_memory_exhausted',503);
   let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>32768)throw failure('invalid_filter');}
   const body=raw?JSON.parse(raw):{};let output:any;
   if(req.method==='POST'&&req.url==='/search') {
    if(!leaseValid||!engine.ready())throw failure('index_unavailable',503);
    const input=search(body),deadline=Date.now()+100;
    while(input.min_receipts.some(r=>BigInt(engine.sequences[r.shard_id].seq)<BigInt(r.seq))){if(Date.now()>=deadline)throw failure('index_not_caught_up',503);await delay(10);if(!engine.ready())throw failure('index_unavailable',503);}
    const c=await adsPool().getConnection();let conditions:Condition[];
    try {const defs=await fields(c);conditions=[];for(const condition of input.conditions){const f=defs.find(v=>v.field_key===condition.field);if(!f)throw failure('invalid_filter');let values:string[]=[];if(condition.values){const [rows]=await c.execute<RowDataPacket[]>(`SELECT term_id FROM ads_business_recall_terms WHERE field_id=? AND value IN (${condition.values.map(()=>'?').join(',')})`,[f.field_id,...condition.values]);values=rows.map(r=>String(r.term_id));}conditions.push({...condition,field:f.field_id,...(condition.values?{values}:{})});}}
    finally {c.release();}
    const target=engine;const remaining=deadline-Date.now();if(remaining<=0)throw failure('recall_deadline_exceeded',504);
    output=await target.open(conditions,input.limit,Date.now(),remaining);queryToken=output.token;handles.set(output.token,target);output.pending_age_ms=target.pendingAge;
    if(Date.now()>deadline)throw failure('recall_deadline_exceeded',504);
   } else if(req.method==='POST'&&req.url==='/next'){if(!leaseValid)throw failure('index_unavailable',503);if(typeof body.token!=='string'||(body.limit!==undefined&&(!Number.isInteger(body.limit)||body.limit<1||body.limit>100)))throw failure('invalid_filter');const target=handles.get(body.token);if(!target)throw failure('query_expired',422);output=await target.next(body.token,body.limit||50);}
   else if(req.method==='POST'&&req.url==='/release'){await handles.get(body.token)?.release(body.token);handles.delete(body.token);output={released:true};}
   else if(req.method==='POST'&&req.url==='/rebuild') {
    if(rebuilding||checkpointing)throw failure('rebuild_in_progress',409);rebuilding=true;output={accepted:true};
    void(async()=>{const shadow=new Engine();try {const ref=await writeSnapshot(shadow,directory,true);await restore(shadow,ref,directory);const until=Date.now()+60*60*1000;do{await synchronize(shadow);if(Date.now()>until)throw failure('rebuild_deadline_exceeded',503);}while(!shadow.ready());const durable=await writeSnapshot(shadow,directory);await renew(durable,shadow.generation);manifest=durable;const old=engine;engine=shadow;setTimeout(()=>void old.close(),5100);}catch{await shadow.close();}finally{rebuilding=false;}})();
   } else throw failure('not_found',404);
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify(output));
  }catch(e:any){if(queryToken){await handles.get(queryToken)?.release(queryToken);handles.delete(queryToken);}res.statusCode=e.status||400;if(res.statusCode===429)res.setHeader('Retry-After','1');res.end(JSON.stringify({error:e.status?e.message:'invalid_filter'}));}finally{if(slot)releaseSlot();}
 });
 server.requestTimeout=5000;
 await new Promise<void>(r=>server.listen(port,process.env.ADS_RECALL_HOST||'127.0.0.1',r));
 const close=async()=>{stopping=true;clearInterval(leaseTimer);server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));while(checkpointing||rebuilding)await delay(10);await engine.close();await adsPool().execute('UPDATE ads_business_recall_consumers SET lease_until=UTC_TIMESTAMP(3) WHERE consumer_id=? AND lease_epoch=?',[consumer,epoch]);};
 return {server,close};
}
if(require.main===module)startRecall().then(({close})=>{console.log('recall service ready');for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>void close().finally(closeAdsDatabase));}).catch(async e=>{console.error(e.status?e.message:'recall startup failed');await closeAdsDatabase();process.exitCode=1;});
