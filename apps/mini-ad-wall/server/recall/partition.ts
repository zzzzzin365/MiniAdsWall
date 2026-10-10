import Bitmap = require('roaring/RoaringBitmap32');
import { performance } from 'perf_hooks';
import { Condition, Document, failure } from './contracts';
import { Node, put, remove, find, iterate, top } from './tree';
const scores=new WeakMap<Document,bigint>();
export function compare(a:Document,b:Document) {
 const score=(d:Document)=>{let v=scores.get(d);if(v===undefined){v=BigInt(d.score.replace('.',''));scores.set(d,v);}return v;};
 const x=score(a),y=score(b);return x===y?a.doc_id-b.doc_id:x>y?-1:1;
}
const byId=(a:Document,b:Document)=>a.doc_id-b.doc_id;
const local=(id:number)=>Math.floor(id/32);
export class Partition {
 seq=0n; private documents:Node<Document>|null=null;private ranking:Node<Document>|null=null;
 private terms=new Map<string,Bitmap>();private presence=new Map<string,Bitmap>();private enabled=new Bitmap();
 constructor(readonly shard:number) {}
 private bitmap(map:Map<string,Bitmap>,key:string) { let b=map.get(key);if(!b)map.set(key,b=new Bitmap());return b; }
 load(doc:Document) {
  if(!Number.isInteger(doc.doc_id)||doc.doc_id<1||doc.doc_id>4294967295||doc.doc_id%32!==this.shard||!/^\d+\.\d{8}$/.test(doc.score)||!/^\d+$/.test(doc.index_revision)||!doc.attributes||typeof doc.enabled!=='boolean'||typeof doc.deleted!=='boolean')throw failure('invalid_index_document',503);
  for(const [field,values]of Object.entries(doc.attributes))if(!/^\d+$/.test(field)||!Array.isArray(values)||values.some(v=>!/^\d+$/.test(v)))throw failure('invalid_index_document',503);
  const old=find(this.documents,doc,byId);
  if(old&&BigInt(old.index_revision)>=BigInt(doc.index_revision))return;
  const id=local(doc.doc_id);
  if(old) {
   for(const [f,values]of Object.entries(old.attributes)){this.presence.get(f)?.delete(id);for(const term of values){const b=this.terms.get(term);b?.delete(id);if(b&&!b.size)this.terms.delete(term);}}
   this.enabled.delete(id);if(old.enabled&&!old.deleted)this.ranking=remove(this.ranking,old,compare);
  }
  // Full immutable document versions are shared by persistent roots; bitmap mutations stay in this worker.
  this.documents=put(this.documents,doc,byId);
  if(!doc.deleted)for(const [f,values]of Object.entries(doc.attributes)){if(values.length)this.bitmap(this.presence,f).add(id);for(const term of values)this.bitmap(this.terms,term).add(id);}
  if(doc.enabled&&!doc.deleted){this.enabled.add(id);this.ranking=put(this.ranking,doc,compare);}
 }
 apply(seq:string,doc:Document) { if(BigInt(seq)!==this.seq+1n)throw failure('index_sequence_gap',503);this.load(doc);this.seq=BigInt(seq); }
 export() { return {seq:String(this.seq),documents:iterate(this.documents)}; }
 cursor(conditions:Condition[],time:number,pageSize:number,cpuBudget=50):Generator<Document> {
  let remaining=cpuBudget,last=performance.now();
  const check=()=>{const now=performance.now();remaining-=now-last;last=now;if(remaining<0)throw failure('query_budget_exceeded',422);};
  const unions=new Map<Condition,Bitmap>();const positives:Bitmap[]=[];
  for(const c of conditions) {
   check();const p=this.presence.get(c.field)||new Bitmap();
   if(c.op==='exists')positives.push(p);
   if(c.op==='in'||c.op==='not_in'){const u=new Bitmap();for(const v of c.values||[]) {const b=this.terms.get(v);if(b)u.orInPlace(b);check();}unions.set(c,u);if(c.op==='in')positives.push(u);else if(c.missing!=='include')positives.push(p);}
  }
  positives.push(this.enabled);positives.sort((a,b)=>a.size-b.size);
  const candidate=positives[0].clone();
  for(const p of positives.slice(1)){candidate.andInPlace(p);check();}
  for(const c of conditions){if(c.op==='not_in')candidate.andNotInPlace(unions.get(c)!);if(c.op==='missing')candidate.andNotInPlace(this.presence.get(c.field)||new Bitmap());check();}
  if(candidate.getSerializationSizeInBytes(true)>8*1024*1024)throw failure('query_budget_exceeded',422);
  const documents=this.documents,ranking=this.ranking,shard=this.shard;
  const eligible=(d:Document)=>!d.starts_at||Date.parse(d.starts_at)<=time;
  const active=(d:Document)=>eligible(d)&&(!d.ends_at||Date.parse(d.ends_at)>time);
  return (function*() {
   if(candidate.size<=5000) {
    let after:Document|undefined;
    while(true) {
     const source=function*(){for(const id of candidate){check();const d=find(documents,{doc_id:id*32+shard} as Document,byId)!;if(active(d)&&(!after||compare(d,after)>0))yield d;}};
     const batch=top(source(),pageSize,compare);if(!batch.length)return;
     for(const d of batch){check();yield d;last=performance.now();}after=batch[batch.length-1];
    }
   } else {
    let visited=0;
    for(const d of iterate(ranking)){check();if(++visited>20000)throw failure('query_budget_exceeded',422);if(candidate.has(local(d.doc_id))&&active(d)){yield d;last=performance.now();}}
   }
  })();
 }
}
