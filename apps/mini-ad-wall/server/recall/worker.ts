import { parentPort, workerData } from 'worker_threads';
import { randomUUID } from 'crypto';
import { Partition } from './partition';
import { Document, failure } from './contracts';
const partitions=new Map<number,Partition>(workerData.shards.map((s:number)=>[s,new Partition(s)]));
const cursors=new Map<string,{time:number;values:Map<number,Generator<Document>>;snapshot?:boolean}>();
function take(cursor:Generator<Document>,limit:number) { const items:Document[]=[];let done=false;while(items.length<limit){const v=cursor.next();if(v.done){done=true;break;}items.push({doc_id:v.value.doc_id,score:v.value.score,index_revision:v.value.index_revision} as Document);}return {items,done}; }
parentPort!.on('message',m=>{
 try {
  for(const [token,c]of cursors)if(!c.snapshot&&Date.now()-c.time>5000)cursors.delete(token);
  let result:any;
  if(m.cmd==='load') { for(const d of m.docs)partitions.get(d.doc_id%32)!.load(d);result=true; }
  else if(m.cmd==='seq') { for(const s of m.sequences)if(partitions.has(s.shard_id))partitions.get(s.shard_id)!.seq=BigInt(s.seq);result=true; }
  else if(m.cmd==='events') {for(const e of m.events)partitions.get(e.shard_id)!.apply(String(e.seq),e.payload);result=true;}
  else if(m.cmd==='status')result=[...partitions].map(([shard_id,p])=>({shard_id,seq:String(p.seq)}));
  else if(m.cmd==='open') {
   if(cursors.size>=32)throw failure('recall_overloaded',429);
   const token=randomUUID(), values=new Map<number,Generator<Document>>();
   for(const [s,p]of partitions)values.set(s,p.cursor(m.conditions,m.time,m.limit));
   cursors.set(token,{time:Date.now(),values});
   result={token,sequences:[...partitions].map(([shard_id,p])=>({shard_id,seq:String(p.seq)})),shards:[...values].map(([shard_id,c])=>({shard_id,...take(c,m.limit)}))};
  } else if(m.cmd==='next') {const c=cursors.get(m.token);if(!c)throw failure('query_expired',422);result=take(c.values.get(m.shard)!,m.limit);}
  else if(m.cmd==='release') {cursors.delete(m.token);result=true;}
  else if(m.cmd==='export') {
   const token=randomUUID(),values=new Map<number,Generator<Document>>(),sequences:any[]=[];
   for(const [s,p]of partitions){const state=p.export();values.set(s,state.documents);sequences.push({shard_id:s,seq:state.seq});}
   cursors.set(token,{time:Date.now(),values,snapshot:true});result={token,sequences};
  } else if(m.cmd==='exportNext') {
   const c=cursors.get(m.token);if(!c)throw failure('snapshot_expired',503);c.time=Date.now();
   const items:Document[]=[];
   for(const [s,it]of c.values){while(items.length<500){const v=it.next();if(v.done){c.values.delete(s);break;}items.push(v.value);}if(items.length===500)break;}
   result={items,done:!c.values.size};if(result.done)cursors.delete(m.token);
  } else throw failure('unknown_command');
  parentPort!.postMessage({id:m.id,result});
 } catch(e:any){parentPort!.postMessage({id:m.id,error:{message:e.message,status:e.status||503}});}
});
