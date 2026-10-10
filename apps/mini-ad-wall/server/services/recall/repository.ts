import { PoolConnection, RowDataPacket, ResultSetHeader } from 'mysql2/promise';
import { attributes, eligibility, Document, Field, Receipt, enabled, failure } from '../../recall/contracts';
const parse=(v:any)=>typeof v==='string'?JSON.parse(v):v;
const iso=(v:any)=>v ? new Date(v).toISOString() : null;
export async function fields(c: PoolConnection): Promise<Field[]> {
 const [rows]=await c.query<RowDataPacket[]>('SELECT field_id,field_key,cardinality,max_values FROM ads_business_recall_fields ORDER BY field_id');
 return rows.map(r=>({field_id:String(r.field_id),field_key:Buffer.isBuffer(r.field_key)?r.field_key.toString():r.field_key,cardinality:r.cardinality,max_values:r.max_values}));
}
export async function lock(c: PoolConnection,id:string,create=false): Promise<any> {
 const [migration]=await c.query<RowDataPacket[]>("SELECT 1 FROM ads_business_migrations WHERE name='schema-ads-recall-v1'");
 if(!migration.length) {if(enabled())throw failure('recall_migration_required',503);return;}
 if(create) {
  const [r]=await c.execute<ResultSetHeader>('INSERT INTO ads_business_recall_docs(ad_id,shard_id) VALUES (?,0)',[id]);
  await c.execute('UPDATE ads_business_recall_docs SET shard_id=doc_id % 32 WHERE doc_id=?',[r.insertId]);
 }
 const [docs]=await c.execute<RowDataPacket[]>('SELECT * FROM ads_business_recall_docs WHERE ad_id=?',[id]);
 if(!docs.length) {
  const [ads]=await c.execute<RowDataPacket[]>('SELECT id FROM ads_business_ads WHERE id=?',[id]);
  if(ads.length) throw failure('recall_migration_required',503);
  return;
 }
 const doc=docs[0];
 await c.execute('SELECT committed_seq FROM ads_business_recall_partition_state WHERE shard_id=? FOR UPDATE',[doc.shard_id]);
 const [current]=await c.execute<RowDataPacket[]>('SELECT * FROM ads_business_recall_docs WHERE doc_id=? FOR UPDATE',[doc.doc_id]);
 return current[0];
}
export async function projections(c:PoolConnection,ids:number[]):Promise<Document[]> {
 if(!ids.length) return [];
 const marks=ids.map(()=>'?').join(',');
 const [rows]=await c.execute<RowDataPacket[]>(`SELECT d.*,a.ranking_score FROM ads_business_recall_docs d LEFT JOIN ads_business_ads a ON a.id=d.ad_id WHERE d.doc_id IN (${marks})`,ids);
 const [values]=await c.execute<RowDataPacket[]>(`SELECT doc_id,field_id,term_id FROM ads_business_ad_attributes WHERE doc_id IN (${marks}) ORDER BY doc_id,field_id,term_id`,ids);
 const grouped=new Map<number,Record<string,string[]>>();
 for(const v of values) { let a=grouped.get(v.doc_id);if(!a)grouped.set(v.doc_id,a=Object.create(null));(a[String(v.field_id)] ||= []).push(String(v.term_id)); }
 return rows.map(r=>({doc_id:r.doc_id,index_revision:String(r.index_revision),attributes:grouped.get(r.doc_id)||{},enabled:!!r.enabled,deleted:!!r.deleted,starts_at:iso(r.starts_at),ends_at:iso(r.ends_at),score:r.ranking_score ?? '0.00000000'}));
}
export async function emit(c:PoolConnection,doc:any,input:any={},deleted=false):Promise<Receipt|undefined> {
 if(!doc) return;
 if(input.attributes!==undefined) {
  const defs=await fields(c), normalized=attributes(input.attributes,defs);
  await c.execute('DELETE FROM ads_business_ad_attributes WHERE doc_id=?',[doc.doc_id]);
  for(const key of Object.keys(normalized).sort()) for(const value of [...normalized[key]].sort()) {
   const f=defs.find(v=>v.field_key===key)!;
   await c.execute('INSERT IGNORE INTO ads_business_recall_terms(field_id,value) VALUES (?,?)',[f.field_id,value]);
   const [terms]=await c.execute<RowDataPacket[]>('SELECT term_id FROM ads_business_recall_terms WHERE field_id=? AND value=?',[f.field_id,value]);
   await c.execute('INSERT INTO ads_business_ad_attributes(doc_id,field_id,term_id) VALUES (?,?,?)',[doc.doc_id,f.field_id,terms[0].term_id]);
  }
 }
 const control=input.eligibility===undefined?{}:eligibility(input.eligibility);
 const starts='starts_at' in control?control.starts_at:iso(doc.starts_at), ends='ends_at' in control?control.ends_at:iso(doc.ends_at);
 if(starts && ends && starts>=ends) throw failure('invalid_attribute');
 await c.execute('UPDATE ads_business_recall_docs SET index_revision=index_revision+1, enabled=?, deleted=?, starts_at=?, ends_at=? WHERE doc_id=?',
 [deleted?false:control.enabled??!!doc.enabled,deleted,starts?new Date(starts):null,ends?new Date(ends):null,doc.doc_id]);
 if(deleted) await c.execute('DELETE FROM ads_business_ad_attributes WHERE doc_id=?',[doc.doc_id]);
 await c.execute('UPDATE ads_business_recall_partition_state SET committed_seq=committed_seq+1 WHERE shard_id=?',[doc.shard_id]);
 const [seq]=await c.execute<RowDataPacket[]>('SELECT committed_seq FROM ads_business_recall_partition_state WHERE shard_id=?',[doc.shard_id]);
 const payload=(await projections(c,[doc.doc_id]))[0];
 const bytes=Buffer.byteLength(JSON.stringify(payload));
 const capacity=process.env.ADS_RECALL_OUTBOX_MAX_BYTES||'17179869184';
 if(!/^\d{1,20}$/.test(capacity)||BigInt(capacity)<32n)throw failure('invalid_recall_capacity',503);
 const [usage]=await c.execute<RowDataPacket[]>('SELECT retained_bytes FROM ads_business_recall_partition_state WHERE shard_id=?',[doc.shard_id]);
 if(BigInt(usage[0].retained_bytes)+BigInt(bytes)>BigInt(capacity)/32n*9n/10n)throw failure('recall_event_capacity_exhausted',503);
 await c.execute('UPDATE ads_business_recall_partition_state SET retained_bytes=retained_bytes+? WHERE shard_id=?',[bytes,doc.shard_id]);
 await c.execute('INSERT INTO ads_business_recall_outbox(shard_id,seq,doc_id,index_revision,event_type,payload,payload_bytes) VALUES (?,?,?,?,?,?,?)',
 [doc.shard_id,seq[0].committed_seq,doc.doc_id,payload.index_revision,deleted?'delete':'upsert',JSON.stringify(payload),bytes]);
 return {shard_id:doc.shard_id,seq:String(seq[0].committed_seq),index_revision:payload.index_revision};
}
export async function decorate(c:PoolConnection,ad:any,doc:any) {
 if(!doc || !ad) return ad;
 const [values]=await c.execute<RowDataPacket[]>('SELECT f.field_key,t.value FROM ads_business_ad_attributes x JOIN ads_business_recall_fields f ON f.field_id=x.field_id JOIN ads_business_recall_terms t ON t.term_id=x.term_id WHERE x.doc_id=? ORDER BY x.field_id,x.term_id',[doc.doc_id]);
 const attrs=Object.create(null);
 for(const v of values) (attrs[Buffer.from(v.field_key).toString()] ||= []).push(Buffer.from(v.value).toString());
 const [row]=await c.execute<RowDataPacket[]>('SELECT enabled,starts_at,ends_at FROM ads_business_recall_docs WHERE doc_id=?',[doc.doc_id]);
 return {...ad,attributes:attrs,eligibility:{enabled:!!row[0].enabled,starts_at:iso(row[0].starts_at),ends_at:iso(row[0].ends_at)}};
}
