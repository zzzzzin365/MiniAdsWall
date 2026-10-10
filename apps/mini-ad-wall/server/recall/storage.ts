import fs from 'fs';
import path from 'path';
import { once } from 'events';
import { createHash,randomUUID } from 'crypto';
import { finished } from 'stream/promises';
import readline from 'readline';
import { RowDataPacket } from 'mysql2/promise';
import { adsPool } from '../services/ads.database';
import { projections } from '../services/recall/repository';
import { Engine } from './engine';
import { failure } from './contracts';
const parse=(x:any)=>typeof x==='string'?JSON.parse(x):x;
async function line(out:fs.WriteStream,value:any,hash:any){const bytes=JSON.stringify(value)+'\n';hash.update(bytes);if(!out.write(bytes))await once(out,'drain');}
export async function writeSnapshot(engine:Engine,directory:string,baseline=false):Promise<string> {
 fs.mkdirSync(directory,{recursive:true});const name=randomUUID()+'.ndjson',target=path.join(directory,name),temp=target+'.tmp';
 const output=fs.createWriteStream(temp,{flags:'wx'}),hash=createHash('sha256');const completed=finished(output);void completed.catch(()=>{});let c:any,packets:any;
 try {
  let sequences:any[];
  if(baseline){c=await adsPool().getConnection();await c.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');await c.query('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY');const [rows]=await c.query('SELECT shard_id,committed_seq AS seq FROM ads_business_recall_partition_state ORDER BY shard_id');sequences=rows.map(r=>({shard_id:r.shard_id,seq:String(r.seq)}));}
  else {const state=await engine.exportStart();sequences=state.sequences;packets=state.packets;}
  await line(output,{format:1,routing:1,schema:1,generation:engine.generation,sequences},hash);
  if(baseline){let after=0;const deadline=Date.now()+30*60*1000;while(true){if(Date.now()>deadline)throw failure('rebuild_deadline_exceeded',503);const [rows]=await c.execute('SELECT doc_id FROM ads_business_recall_docs WHERE doc_id>? ORDER BY doc_id LIMIT 500',[after]);if(!rows.length)break;for(const d of await projections(c,rows.map(r=>r.doc_id)))await line(output,d,hash);after=rows[rows.length-1].doc_id;}await c.commit();c.release();c=undefined;}
  else for(let i=0;i<packets.length;i++){while(true){const batch=await engine.workers[i].call('exportNext',{token:packets[i].token});for(const d of batch.items)await line(output,d,hash);if(batch.done)break;}}
  output.end();await completed;const handle=await fs.promises.open(temp,'r+');await handle.sync();await handle.close();await fs.promises.rename(temp,target);
  const manifest={file:name,sha256:hash.digest('hex'),format:1,sequences,generation:engine.generation,created_at:new Date().toISOString()};
  const manifestPath=target+'.manifest.json';await fs.promises.writeFile(manifestPath+'.tmp',JSON.stringify(manifest));const meta=await fs.promises.open(manifestPath+'.tmp','r+');await meta.sync();await meta.close();await fs.promises.rename(manifestPath+'.tmp',manifestPath);
  return manifestPath;
 } catch(e){if(c){await c.rollback().catch(()=>{});c.release();}output.destroy();await fs.promises.rm(temp,{force:true}).catch(()=>{});throw e;}
 finally {if(packets)await Promise.all(packets.map((p,i)=>engine.workers[i].call('release',{token:p.token}).catch(()=>{})));}
}
export async function restore(engine:Engine,manifestPath:string,directory:string,depth=0) {
 if(path.dirname(path.resolve(manifestPath))!==path.resolve(directory))throw failure('invalid_snapshot_path',503);
 if(depth>32)throw failure('snapshot_chain_too_long',503);
 const manifest=JSON.parse(await fs.promises.readFile(manifestPath,'utf8'));
 if(manifest.base){if(path.basename(manifest.base)!==manifest.base)throw failure('invalid_snapshot_path',503);await restore(engine,path.join(directory,manifest.base),directory,depth+1);}
 if(manifest.format!==1||path.basename(manifest.file)!==manifest.file)throw failure('unsupported_snapshot',503);
 const file=path.join(directory,manifest.file),hash=createHash('sha256');
 for await(const bytes of fs.createReadStream(file))hash.update(bytes);
 if(hash.digest('hex')!==manifest.sha256)throw failure('snapshot_checksum_mismatch',503);
 const input=readline.createInterface({input:fs.createReadStream(file),crlfDelay:Infinity});let header:any,batch:any[]=[];
 for await(const raw of input){const value=JSON.parse(raw);if(!header){header=value;if(header.format!==1||header.routing!==1||header.schema!==1||JSON.stringify(header.sequences)!==JSON.stringify(manifest.sequences))throw failure('unsupported_snapshot',503);}else {batch.push(value);if(batch.length===500){if(header.kind==='delta')await engine.apply(batch);else await engine.load(batch);batch=[];}}}
 if(batch.length){if(header.kind==='delta')await engine.apply(batch);else await engine.load(batch);}if(!header||header.sequences.length!==32||header.sequences.some((s,i)=>s.shard_id!==i||!/^\d+$/.test(s.seq)))throw failure('invalid_snapshot',503);
 if(header.kind==='delta'){if(JSON.stringify(engine.sequences)!==JSON.stringify(header.sequences))throw failure('index_sequence_gap',503);}else await engine.setSequences(header.sequences);
}
export async function poll(engine:Engine) {
 const c=await adsPool().getConnection();try {
  const [water]=await c.query<RowDataPacket[]>('SELECT shard_id,committed_seq,UTC_TIMESTAMP(3) AS db_time FROM ads_business_recall_partition_state ORDER BY shard_id');
  if(water.length!==32||Math.abs(Date.now()-new Date(water[0].db_time).getTime())>500)throw failure('index_watermark_unavailable',503);
  const events:any[]=[];let age=0;
  for(const state of engine.sequences) {
   if(BigInt(water[state.shard_id].committed_seq)>BigInt(state.seq)){
    const [rows]=await c.execute<RowDataPacket[]>('SELECT shard_id,seq,payload,created_at FROM ads_business_recall_outbox WHERE shard_id=? AND seq>? ORDER BY seq LIMIT 500',[state.shard_id,state.seq]);
    if(!rows.length||BigInt(rows[0].seq)!==BigInt(state.seq)+1n)throw failure('index_sequence_gap',503);
    events.push(...rows.map(r=>({...r,seq:String(r.seq),payload:parse(r.payload)})));
   }
  }
  if(events.length)await engine.apply(events);
  for(const state of engine.sequences)if(BigInt(water[state.shard_id].committed_seq)>BigInt(state.seq)){
   const [oldest]=await c.execute<RowDataPacket[]>('SELECT created_at FROM ads_business_recall_outbox WHERE shard_id=? AND seq>? ORDER BY seq LIMIT 1',[state.shard_id,state.seq]);
   if(oldest.length)age=Math.max(age,Date.now()-new Date(oldest[0].created_at).getTime());
  }
  engine.pendingAge=age;engine.lastPoll=Date.now();
 } catch(e){engine.fatal=true;throw e;}finally {c.release();}
}

// Persist only committed events since the last durable version; full snapshots are rebuilds.
export async function writeDelta(engine:Engine,directory:string,base:string):Promise<string> {
 const previous=JSON.parse(await fs.promises.readFile(base,'utf8'));
 const sequences=engine.sequences.map(s=>({...s}));
 if(sequences.every(s=>s.seq===previous.sequences[s.shard_id].seq))return base;
 if((previous.depth||0)>=30)return writeSnapshot(engine,directory);
 const name=randomUUID()+'.ndjson',target=path.join(directory,name),out=fs.createWriteStream(target+'.tmp',{flags:'wx'}),hash=createHash('sha256');
 const completed=finished(out);void completed.catch(()=>{});const c=await adsPool().getConnection();
 try {
  await line(out,{format:1,routing:1,schema:1,kind:'delta',generation:engine.generation,sequences},hash);
  for(const state of sequences){let after=BigInt(previous.sequences[state.shard_id].seq);while(after<BigInt(state.seq)){
   const [rows]=await c.execute<RowDataPacket[]>('SELECT shard_id,seq,payload FROM ads_business_recall_outbox WHERE shard_id=? AND seq>? AND seq<=? ORDER BY seq LIMIT 500',[state.shard_id,String(after),state.seq]);
   if(!rows.length)throw failure('index_sequence_gap',503);
   for(const row of rows){if(BigInt(row.seq)!==after+1n)throw failure('index_sequence_gap',503);await line(out,{shard_id:row.shard_id,seq:String(row.seq),payload:parse(row.payload)},hash);after=BigInt(row.seq);}
  }}
  out.end();await completed;const file=await fs.promises.open(target+'.tmp','r+');await file.sync();await file.close();await fs.promises.rename(target+'.tmp',target);
  const manifest={file:name,sha256:hash.digest('hex'),format:1,sequences,generation:engine.generation,base:path.basename(base),depth:(previous.depth||0)+1,created_at:new Date().toISOString()};
  const ref=target+'.manifest.json';await fs.promises.writeFile(ref+'.tmp',JSON.stringify(manifest));const meta=await fs.promises.open(ref+'.tmp','r+');await meta.sync();await meta.close();await fs.promises.rename(ref+'.tmp',ref);return ref;
 }catch(e){out.destroy();await fs.promises.rm(target+'.tmp',{force:true}).catch(()=>{});throw e;}finally{c.release();}
}
