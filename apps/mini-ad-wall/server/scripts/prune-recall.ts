import fs from 'fs';
import path from 'path';
import { RowDataPacket } from 'mysql2/promise';
import { adsPool,closeAdsDatabase } from '../services/ads.database';
import { failure } from '../recall/contracts';
export async function pruneRecall(directory:string) {
 const c=await adsPool().getConnection();let locked=false,deleted=0;
 try {
  const [locks]=await c.query<RowDataPacket[]>("SELECT GET_LOCK(CONCAT('ads:',LEFT(SHA2(DATABASE(),256),50)),30) AS ok");if(Number(locks[0].ok)!==1)throw failure('prune_lock_timeout',503);locked=true;
  const [consumers]=await c.query<RowDataPacket[]>('SELECT durable_manifest_ref FROM ads_business_recall_consumers');
  if(!consumers.length||consumers.some(v=>!v.durable_manifest_ref))return {deleted:0,reason:'no_durable_watermark'};
  const protectedRefs=new Set<string>(consumers.map(v=>v.durable_manifest_ref));
  for(const file of await fs.promises.readdir(directory))if(file.endsWith('.manifest.json')){
   const ref=path.join(directory,file),m=JSON.parse(await fs.promises.readFile(ref,'utf8'));
   if(Date.now()-Date.parse(m.created_at)<=86400000)protectedRefs.add(ref);
  }
  const minimum=Array<bigint>(32).fill(18446744073709551615n);
  for(const ref of protectedRefs){if(path.dirname(path.resolve(ref))!==path.resolve(directory))throw failure('shared_snapshot_directory_required',503);const m=JSON.parse(await fs.promises.readFile(ref,'utf8'));if(!Array.isArray(m.sequences)||m.sequences.length!==32)throw failure('invalid_snapshot',503);for(const s of m.sequences)minimum[s.shard_id]=minimum[s.shard_id]<BigInt(s.seq)?minimum[s.shard_id]:BigInt(s.seq);}
  for(let shard=0;shard<32;shard++)while(true){await c.beginTransaction();try {
   await c.execute('SELECT retained_bytes FROM ads_business_recall_partition_state WHERE shard_id=? FOR UPDATE',[shard]);
   const [rows]=await c.execute<RowDataPacket[]>('SELECT seq,payload_bytes FROM ads_business_recall_outbox WHERE shard_id=? AND seq<=? AND created_at<DATE_SUB(UTC_TIMESTAMP(3),INTERVAL 24 HOUR) ORDER BY seq LIMIT 1000',[shard,String(minimum[shard])]);
   if(!rows.length){await c.commit();break;}
   await c.execute('DELETE FROM ads_business_recall_outbox WHERE shard_id=? AND seq IN ('+rows.map(()=>'?').join(',')+')',[shard,...rows.map(r=>r.seq)]);
   await c.execute('UPDATE ads_business_recall_partition_state SET retained_bytes=GREATEST(0,retained_bytes-?) WHERE shard_id=?',[rows.reduce((sum,r)=>sum+r.payload_bytes,0),shard]);await c.commit();deleted+=rows.length;
  }catch(e){await c.rollback();throw e;}}
  return {deleted};
 }finally{if(locked)await c.query("SELECT RELEASE_LOCK(CONCAT('ads:',LEFT(SHA2(DATABASE(),256),50)))").catch(()=>{});c.release();}
}
if(require.main===module)pruneRecall(path.resolve(process.env.ADS_RECALL_SNAPSHOT_DIR||'recall-snapshots')).then(v=>console.log(JSON.stringify(v))).catch(()=>{console.error('recall prune failed; retained all unconfirmed events');process.exitCode=1;}).finally(closeAdsDatabase);
