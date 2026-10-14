const {test} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {database} = require('./helpers/mysql.cjs');
process.env.MERCHANT_WORKSPACE_ENABLED = 'true';
process.env.ADS_OPERATOR_TOKENS = JSON.stringify({operator:'rag-test-only',outsider:'rag-outsider-only'});
const {migrateMarketingSchema} = require('../dist/services/marketing/migrations');
const {studioSchema} = require('../dist/services/merchant/studio/migration');
const {seedMarketingScope} = require('../dist/services/marketing/seed');
const {withAdsConnection} = require('../dist/services/ads.database');
const {put,queue} = require('../dist/services/merchant/studio/store');
const {mediaCorpus} = require('../dist/services/merchant/studio/knowledge');
const {tick} = require('../dist/services/merchant/studio/worker');
const app = require('../dist/app').default;

test('media RAG uses authorized historical sources, latest versions, and persists citations', async t => {
  const db = await database(t); if (!db) return;
  t.after(() => db.stop());
  await migrateMarketingSchema(); await studioSchema(true);
  for (const b of ['rag-brand','foreign-brand']) await seedMarketingScope({brand_id:b,
    product_id:b+'-product',principal:'operator',name:b,environment:'synthetic',ad_ids:b==='rag-brand'?['1']:['2']});
  const server = app.listen(0,'127.0.0.1'); await new Promise(r => server.once('listening',r));
  const base = 'http://127.0.0.1:'+server.address().port+'/api/merchant/v1/studio';
  const call = async (path, body, token='rag-test-only') => {
    const response = await fetch(base+path,{method:body===undefined?'GET':'POST',headers:{
      Authorization:'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':randomUUID()},
      body:body===undefined?undefined:JSON.stringify(body)});
    return {status:response.status,data:await response.json()};
  };
  async function source(brand, kind, status='succeeded') {
    return withAdsConnection(async c => {
      const asset = await put(c,'operator',brand,'synthetic','asset',
        {filename:'test-'+randomUUID(),mime:kind==='video'?'video/mp4':'image/png'});
      const analysis = await queue(c,'operator',{...asset,environment:'synthetic'},'text',
        {kind:'asset_analysis',asset_id:asset.id,model:'doubao-seed-2-1-lite-260915'});
      const result = {asset_id:asset.id,model:'doubao-seed-2-1-lite-260915',status:'succeeded',frame_count:1,
        subject:'historical toothbrush',observations:[{description:'white handle',frame_index:0,
        time_ms:kind==='video'?1500:null,asset_id:asset.id}],source_claims:['antibacterial, unverified'],
        marketing_summary:'product closeup',uncertainties:['efficacy not verified'],
        limitations:kind==='video'?['sampled frames, no audio']:[]};
      await c.execute('UPDATE merchant_studio_jobs SET status=?,result=? WHERE id=?',
        [status,JSON.stringify(result),analysis.id]);
      await put(c,'operator',brand,'synthetic','asset',{...asset.data,analysis_job_id:analysis.id},asset.id,2);
      return {asset,analysis,result};
    });
  }
  try {
    const image = await source('rag-brand','image');
    const video = await source('rag-brand','video');
    const foreign = await source('foreign-brand','image');
    await source('rag-brand','image','failed');
    let c = await withAdsConnection(c => mediaCorpus(c,'operator','rag-brand','synthetic'));
    assert.equal(c.sources.length,2);
    assert.ok(c.sources.every(s => s.asset_id !== foreign.asset.id));
    assert.ok(c.sources.some(s => s.asset_id===video.asset.id && s.observations[0].time_ms===1500));
    await assert.rejects(withAdsConnection(c => mediaCorpus(c,'outsider','rag-brand','synthetic')),/权限/);
    // A newer asset revision invalidates the previous analysis pointer.
    await withAdsConnection(c => put(c,'operator','rag-brand','synthetic','asset',image.asset.data,image.asset.id,3));
    c = await withAdsConnection(c => mediaCorpus(c,'operator','rag-brand','synthetic'));
    assert.equal(c.sources.length,1); assert.equal(c.sources[0].asset_id,video.asset.id);
    const brief = (await call('/briefs',{brand_id:'rag-brand',subject_type:'product',
      subject_description:'toothbrush',objective:'traffic',idea:'historical video',confirmed_facts:[],asset_refs:[]})).data;
    const session = (await call('/sessions',{brief_id:brief.id,brief_revision:1})).data;
    assert.equal((await call('/sessions/'+session.id+'/turns',{expected_revision:1,message:'find earlier material',
      media_knowledge:{brand_id:'foreign-brand'}})).status,422);
    const turn = await call('/sessions/'+session.id+'/turns',{expected_revision:1,message:'find earlier material'});
    assert.equal(turn.status,202);
    await tick('synthetic',async (path,body) => {
      assert.equal(path,'/studio/generate');
      assert.equal(body.asset_understandings.length,0); // historical source, not current attachment
      assert.equal(body.media_knowledge.brand_id,'rag-brand');
      assert.equal(body.media_knowledge.sources.length,1);
      const s = body.media_knowledge.sources[0];
      const evidence = {...s,id:'a'.repeat(64),frame_index:0,time_ms:1500,excerpt:'white handle',score:.8};
      delete evidence.observations;
      return {summary:'参考历史视频中的白色手柄',questions:[],concepts:[{title:'牙刷',scenario:'桌面',
        copy:'牙刷特写',script:'白色手柄特写',video_prompt:'白色手柄',assumptions:[]}],
        delivery:{objective:'traffic',audience_hypotheses:[],test_plan:'test',rationale:'sample',missing_data:[],
        bid:{channel:null,billing_model:'unknown',currency:'CNY',amount_minor:null,status:'insufficient_basis'}},
        model:'deepseek-v4-1-flash-260910',policy:'seed-understanding-v2',status:'draft',
        understanding:{status:'seed_understood',source:'seed_analysis',limitations:['no audio']},
        source_ids:[evidence.id],media_rag:{status:'retrieved',method:'caption-vector-v1',corpus_assets:1,
          corpus_truncated:false,retrieved:[evidence],citations:[evidence]}};
    });
    const job = (await call('/jobs/'+turn.data.job.id)).data;
    assert.equal(job.status,'succeeded');assert.equal(job.result.media_rag.citations[0].time_ms,1500);
    const [recipes] = await db.connection.query("SELECT data FROM merchant_studio_objects WHERE kind='recipe'");
    const recipe = typeof recipes[0].data==='string'?JSON.parse(recipes[0].data):recipes[0].data;
    assert.equal(recipe.media_sources[0].asset_id,video.asset.id);
    assert.equal((await call('/jobs/'+turn.data.job.id,undefined,'rag-outsider-only')).status,403);
    // Revocation between enqueue and execution prevents any provider request.
    const next = await call('/sessions/'+session.id+'/turns',{expected_revision:2,message:'next turn'});
    assert.equal(next.status,202);
    await db.connection.execute("DELETE FROM marketing_memberships WHERE brand_id='rag-brand'");
    await tick('synthetic',async () => {throw Error('must not call after revocation');});
    const [rows] = await db.connection.execute('SELECT status FROM merchant_studio_jobs WHERE id=?',[next.data.job.id]);
    assert.equal(rows[0].status,'blocked');
  } finally {
    await new Promise(r => server.close(r)); await db.stop();
  }
});
