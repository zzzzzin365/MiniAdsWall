import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { authorize, decode } from './store';
import { validateUnderstanding, validateVisualEmbeddings, nativeMediaRag } from './contracts';

// Successful understanding jobs are durable source knowledge. Chroma is a
// derived index; never send a browser-controlled corpus, brand, or environment.
export async function mediaCorpus(c: PoolConnection, principal: string, brand: string,
  environment: string, attached: string[] = []) {
  await authorize(c, principal, brand, environment, true);
  const native = nativeMediaRag();
  const priority = attached.length ? `CASE WHEN o.id IN (${attached.map(() => '?').join(',')}) THEN 0 ELSE 1 END,` : '';
  const [rows] = await c.execute<RowDataPacket[]>(
    `SELECT o.id,o.revision,o.data,j.id AS analysis_job_id,j.status AS analysis_status,j.result
     FROM merchant_studio_objects o JOIN merchant_studio_jobs j
       ON j.id=JSON_UNQUOTE(JSON_EXTRACT(o.data,'$.analysis_job_id'))
       AND j.brand_id=o.brand_id AND j.environment=o.environment
     WHERE o.kind='asset' AND o.brand_id=? AND o.environment=?
       AND ${native ? "(j.status='succeeded' OR (j.status='failed' AND JSON_EXTRACT(j.result,'$.visual_embeddings') IS NOT NULL))" : "j.status='succeeded'"} AND JSON_UNQUOTE(JSON_EXTRACT(j.input,'$.kind'))='asset_analysis'
       AND NOT EXISTS (SELECT 1 FROM merchant_studio_objects newer
         WHERE newer.id=o.id AND newer.revision>o.revision)
     ORDER BY ${priority}j.updated_at DESC,o.id LIMIT 101`,
    [brand, environment, ...attached]);
  const sources = [];
  for (const row of rows.slice(0, 100)) {
    const asset = decode(row.data), analysis = row.result ? decode(row.result) : {};
    const understood = row.analysis_status === 'succeeded' && validateUnderstanding(analysis) && analysis.asset_id === row.id;
    const visual = analysis?.asset_id === row.id && validateVisualEmbeddings(analysis?.visual_embeddings)
      ? analysis.visual_embeddings : undefined;
    if ((!understood && !(native && visual)) ||
        !['image/jpeg', 'image/png', 'image/webp', 'video/mp4'].includes(asset.mime)) continue;
    if (native && !visual) throw Error('native_media_reindex_required');
    sources.push({asset_id: row.id, asset_revision: row.revision, analysis_job_id: row.analysis_job_id,
      kind: asset.mime === 'video/mp4' ? 'video' : 'image',
      subject: understood ? analysis.subject : '素材 ' + row.id,
      marketing_summary: understood ? analysis.marketing_summary : '',
      observations: understood ? analysis.observations.map((o: any) => ({description: o.description,
        frame_index: o.frame_index, time_ms: o.time_ms})) : [],
      source_claims: understood ? analysis.source_claims : [],
      uncertainties: understood ? analysis.uncertainties : ['仅已完成图像向量索引，尚无Seed画面理解'],
      limitations: understood ? analysis.limitations : ['向量相似不代表画面事实或商品功效已核验'],
      ...(native ? {visual_embeddings:visual,understanding_available:!!understood} : {})});
  }
  return {brand_id: brand, environment, sources, truncated: rows.length > 100};
}
