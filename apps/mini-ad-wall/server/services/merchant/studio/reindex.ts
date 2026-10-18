/** Backfill raw pixel embeddings only. Never submits a paid Seed request. */
import {RowDataPacket} from 'mysql2/promise';
import {withAdsConnection} from '../../ads.database';
import {authorize, object, decode} from './store';
import {analysisInput} from './understanding';
import {studioRequest, Provider} from './worker';
import {validateVisualFrames} from './contracts';

export async function reindexBrandImages(brand: string, environment: string, principal: string,
  provider: Provider = studioRequest) {
  const jobs = await withAdsConnection(async c => {
    await authorize(c,principal,brand,environment,true);
    const [rows] = await c.execute<RowDataPacket[]>(
      `SELECT o.id AS asset_id,o.revision,j.id,j.input,j.result,j.status FROM merchant_studio_objects o
       JOIN merchant_studio_jobs j ON j.id=JSON_UNQUOTE(JSON_EXTRACT(o.data,'$.analysis_job_id'))
         AND j.brand_id=o.brand_id AND j.environment=o.environment
       WHERE o.kind='asset' AND o.brand_id=? AND o.environment=?
         AND j.status IN ('succeeded','failed')
         AND JSON_UNQUOTE(JSON_EXTRACT(j.input,'$.kind'))='asset_analysis'
         AND NOT EXISTS(SELECT 1 FROM merchant_studio_objects n WHERE n.id=o.id AND n.revision>o.revision)
       ORDER BY o.id LIMIT 101`,[brand,environment]);
    if (rows.length>100) throw Error('reindex_batch_limit');
    return rows;
  });
  const updated = [];
  for (const job of jobs) {
    const input = await analysisInput({...job,input:decode(job.input),created_by:principal,brand_id:brand,environment});
    const result = await provider('/studio/embed',input);
    if (result.asset_id!==job.asset_id || !validateVisualFrames(result.visual_embeddings,input.frames))
      throw Error('invalid_visual_embeddings');
    await withAdsConnection(async c => {
      await c.beginTransaction();
      try {
        await authorize(c,principal,brand,environment,true);
        const asset=await object(c,job.asset_id,'asset',environment);
        if (asset.brand_id!==brand || asset.revision!==job.revision || asset.data.analysis_job_id!==job.id)
          throw Error('asset_changed');
        const [current] = await c.execute<RowDataPacket[]>('SELECT status,result FROM merchant_studio_jobs WHERE id=? AND brand_id=? AND environment=? FOR UPDATE',[job.id,brand,environment]);
        if (!current.length || !['succeeded','failed'].includes(current[0].status)) throw Error('analysis_changed');
        const previous = current[0].result ? decode(current[0].result) : {};
        await c.execute('UPDATE merchant_studio_jobs SET result=?,updated_at=UTC_TIMESTAMP(3) WHERE id=?',
          [JSON.stringify({...previous,asset_id:job.asset_id,visual_embeddings:result.visual_embeddings}),job.id]);
        await c.commit();
      } catch(error) {await c.rollback();throw error;}
    });
    updated.push({asset_id:job.asset_id,frames:result.visual_embeddings.length});
  }
  return {brand_id:brand,environment,updated,paid_model_requests:0};
}
