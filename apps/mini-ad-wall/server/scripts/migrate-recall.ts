import fs from 'fs';
import path from 'path';
import { createHash } from 'crypto';
import { RowDataPacket } from 'mysql2/promise';
import { adsPool, closeAdsDatabase } from '../services/ads.database';
export async function migrateRecall() {
 const c = await adsPool().getConnection();
 const root = path.resolve(__dirname, fs.existsSync(path.join(__dirname, '../package.json')) ? '..' : '../..');
 const sql = fs.readFileSync(path.join(root,'migrations/002_ads_recall.sql'),'utf8');
 const checksum = createHash('sha256').update(sql).digest('hex');
 let locked = false;
 try {
  const [lock] = await c.query<RowDataPacket[]>("SELECT GET_LOCK(CONCAT('ads:', LEFT(SHA2(DATABASE(),256),50)),30) AS ok");
  if (Number(lock[0].ok)!==1) throw new Error('migration_lock_timeout'); locked=true;
  const [old] = await c.query<RowDataPacket[]>("SELECT checksum FROM ads_business_migrations WHERE name='schema-ads-recall-v1'");
  if (old.length && old[0].checksum!==checksum) throw new Error('migration_checksum_mismatch');
  for (const statement of sql.split(';').map(s=>s.trim()).filter(Boolean)) await c.query(statement);
  const [columns]=await c.query<RowDataPacket[]>("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ads_business_operations' AND COLUMN_NAME='recall_receipt'");
  if (!columns.length) await c.query('ALTER TABLE ads_business_operations ADD COLUMN recall_receipt JSON NULL');
  for(let i=0;i<32;i++) await c.execute('INSERT IGNORE INTO ads_business_recall_partition_state(shard_id) VALUES (?)',[i]);
  for (const [key, cardinality] of [['region','scalar'],['category','scalar'],['tags','multi']]) await c.execute('INSERT IGNORE INTO ads_business_recall_fields(field_key,cardinality) VALUES (?,?)',[key,cardinality]);
  // The migration must run before enabling the new writer. Never overwrite existing eligibility.
  let after='';
  while(true) {
   const [rows]=await c.execute<RowDataPacket[]>('SELECT id FROM ads_business_ads WHERE id > ? ORDER BY id LIMIT 500',[after]);
   if(!rows.length) break;
   for(const row of rows) await c.execute('INSERT IGNORE INTO ads_business_recall_docs(ad_id) VALUES (?)',[row.id]);
   await c.execute('UPDATE ads_business_recall_docs SET shard_id=doc_id % 32 WHERE index_revision=0 AND ad_id IN ('+rows.map(()=>'?').join(',')+')', rows.map(r=>r.id));
   after=rows[rows.length-1].id;
  }
  await c.execute("INSERT IGNORE INTO ads_business_migrations(name,checksum) VALUES ('schema-ads-recall-v1',?)",[checksum]);
 } finally { if(locked) await c.query("SELECT RELEASE_LOCK(CONCAT('ads:', LEFT(SHA2(DATABASE(),256),50)))").catch(()=>{}); c.release(); }
}
if(require.main===module) migrateRecall().then(()=>console.log('recall migration complete')).catch(e=>{console.error(e.code ? 'recall migration failed' : e.message);process.exitCode=1;}).finally(closeAdsDatabase);
