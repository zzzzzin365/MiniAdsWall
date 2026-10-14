import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { authorize, decode } from './store';
import { validateUnderstanding } from './contracts';

// Successful understanding jobs are durable source knowledge. Chroma is a
// derived index; never send a browser-controlled corpus, brand, or environment.
export async function mediaCorpus(c: PoolConnection, principal: string, brand: string,
  environment: string, attached: string[] = []) {
  await authorize(c, principal, brand, environment, true);
  const priority = attached.length ? `CASE WHEN o.id IN (${attached.map(() => '?').join(',')}) THEN 0 ELSE 1 END,` : '';
  const [rows] = await c.execute<RowDataPacket[]>(
    `SELECT o.id,o.revision,o.data,j.id AS analysis_job_id,j.result
     FROM merchant_studio_objects o JOIN merchant_studio_jobs j
       ON j.id=JSON_UNQUOTE(JSON_EXTRACT(o.data,'$.analysis_job_id'))
       AND j.brand_id=o.brand_id AND j.environment=o.environment
     WHERE o.kind='asset' AND o.brand_id=? AND o.environment=?
       AND j.status='succeeded' AND JSON_UNQUOTE(JSON_EXTRACT(j.input,'$.kind'))='asset_analysis'
       AND NOT EXISTS (SELECT 1 FROM merchant_studio_objects newer
         WHERE newer.id=o.id AND newer.revision>o.revision)
     ORDER BY ${priority}j.updated_at DESC,o.id LIMIT 101`,
    [brand, environment, ...attached]);
  const sources = [];
  for (const row of rows.slice(0, 100)) {
    const asset = decode(row.data), analysis = decode(row.result);
    if (!validateUnderstanding(analysis) || analysis.asset_id !== row.id ||
        !['image/jpeg', 'image/png', 'image/webp', 'video/mp4'].includes(asset.mime)) continue;
    sources.push({asset_id: row.id, asset_revision: row.revision, analysis_job_id: row.analysis_job_id,
      kind: asset.mime === 'video/mp4' ? 'video' : 'image', subject: analysis.subject,
      marketing_summary: analysis.marketing_summary,
      observations: analysis.observations.map((o: any) => ({description: o.description,
        frame_index: o.frame_index, time_ms: o.time_ms})),
      source_claims: analysis.source_claims, uncertainties: analysis.uncertainties,
      limitations: analysis.limitations});
  }
  return {brand_id: brand, environment, sources, truncated: rows.length > 100};
}
