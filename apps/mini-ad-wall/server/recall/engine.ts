import { Worker } from 'worker_threads';
import path from 'path';
import { randomUUID } from 'crypto';
import { Condition, Document, failure } from './contracts';
import { compare } from './partition';
export class RPC {
 worker:Worker;private counter=0;private pending=new Map<number,{resolve:Function;reject:Function;timer:NodeJS.Timeout}>();dead=false;
 constructor(readonly shards:number[]) {
  this.worker=new Worker(path.join(__dirname,'worker.js'),{workerData:{shards}});
  this.worker.on('message',m=>{const p=this.pending.get(m.id);if(!p)return;clearTimeout(p.timer);this.pending.delete(m.id);m.error?p.reject(failure(m.error.message,m.error.status)):p.resolve(m.result);});
  const die=()=>{this.dead=true;for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(failure('index_worker_unavailable',503));}this.pending.clear();};
  this.worker.on('error',die);this.worker.on('exit',die);
 }
 call(cmd:string,data:any={},timeout=5000):Promise<any> {
  if(this.dead)return Promise.reject(failure('index_worker_unavailable',503));
  return new Promise((resolve,reject)=>{const id=++this.counter,timer=setTimeout(()=>{this.pending.delete(id);reject(failure('recall_deadline_exceeded',504));},timeout);this.pending.set(id,{resolve,reject,timer});this.worker.postMessage({id,cmd,...data});});
 }
 async close(){await this.worker.terminate();}
}
export class Engine {
 readonly generation=randomUUID();workers:RPC[];sequences=Array.from({length:32},(_,shard_id)=>({shard_id,seq:'0'}));
 lastPoll=0;pendingAge=0;fatal=false;
 private queries=new Map<string,any>();
 constructor(){this.workers=Array.from({length:4},(_,i)=>new RPC(Array.from({length:8},(_,j)=>i+j*4)));}
 private owner(shard:number){return this.workers[shard%4];}
 async load(docs:Document[]){await Promise.all(this.workers.map(w=>w.call('load',{docs:docs.filter(d=>w.shards.includes(d.doc_id%32))})));}
 async setSequences(sequences:any[]){await Promise.all(this.workers.map(w=>w.call('seq',{sequences})));this.sequences=sequences;}
 async apply(events:any[]) {await Promise.all(this.workers.map(w=>w.call('events',{events:events.filter(e=>w.shards.includes(e.shard_id))})));this.sequences=(await Promise.all(this.workers.map(w=>w.call('status')))).flat().sort((a,b)=>a.shard_id-b.shard_id);}
 ready(){return !this.fatal&&!this.workers.some(w=>w.dead)&&Date.now()-this.lastPoll<=1000&&this.pendingAge<=2000;}
 async open(conditions:Condition[],limit:number,time:number,timeout=100) {
  this.expire();if(this.queries.size>=32)throw failure('recall_overloaded',429);
  const results=await Promise.allSettled(this.workers.map(w=>w.call('open',{conditions,limit,time},timeout)));
  if(results.some(r=>r.status==='rejected')) {await Promise.all(results.map((r,i)=>r.status==='fulfilled'?this.workers[i].call('release',{token:r.value.token}).catch(()=>{}):undefined));throw (results.find(r=>r.status==='rejected') as PromiseRejectedResult).reason;}
  const packets=results.map(r=>(r as PromiseFulfilledResult<any>).value), token=randomUUID();
  const buffers=new Map<number,any>();packets.forEach((p,i)=>p.shards.forEach(s=>buffers.set(s.shard_id,{...s,offset:0,worker:this.workers[i],token:p.token})));
  this.queries.set(token,{buffers,packets,created:Date.now(),limit,sequences:packets.flatMap(p=>p.sequences).sort((a,b)=>a.shard_id-b.shard_id)});
  return {token,...await this.next(token,limit),snapshot:{generation_id:this.generation,schema_version:1,routing_version:1,partitions:this.queries.get(token).sequences.map(s=>({shard_id:s.shard_id,applied_seq:s.seq})),query_time:new Date(time).toISOString()}};
 }
 async next(token:string,limit:number) {
  const q=this.queries.get(token);if(!q||Date.now()-q.created>5000)throw failure('query_expired',422);
  const items:any[]=[];
  while(items.length<limit) {
   await Promise.all([...q.buffers.values()].map(async b=>{if(b.offset===b.items.length&&!b.done){const page=await b.worker.call('next',{token:b.token,shard:b.shard_id,limit:q.limit},100);Object.assign(b,page,{offset:0});}}));
   let best:any;
   for(const b of q.buffers.values())if(b.offset<b.items.length&&(!best||compare(b.items[b.offset],best.items[best.offset])<0))best=b;
   if(!best)break;
   items.push(best.items[best.offset++]);
  }
  return {items,candidate_exhausted:[...q.buffers.values()].every(b=>b.done&&b.offset===b.items.length)};
 }
 async release(token:string){const q=this.queries.get(token);if(!q)return;this.queries.delete(token);await Promise.all(q.packets.map((p,i)=>this.workers[i].call('release',{token:p.token}).catch(()=>{})));}
 hasQuery(token:string){return this.queries.has(token);}
 expire(){for(const [token,q]of this.queries)if(Date.now()-q.created>5000)void this.release(token);}
 async exportStart(){const packets=await Promise.all(this.workers.map(w=>w.call('export')));return {sequences:packets.flatMap(p=>p.sequences).sort((a,b)=>a.shard_id-b.shard_id),packets};}
 async close(){await Promise.all(this.workers.map(w=>w.close()));}
}
