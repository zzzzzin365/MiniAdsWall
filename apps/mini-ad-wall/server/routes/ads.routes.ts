import { adProjection } from '../services/ad-projection';
import { listMaterials, getMaterial, changeMaterial, createMaterial } from '../services/materials.service';
import { operationStatus, mutate } from '../middlewares/businessBoundary';
import { findAds, listPage } from '../services/recall/search';
import { withAdsConnection } from '../services/ads.database';
import { RowDataPacket } from 'mysql2/promise';
import Router from 'koa-router';
import adsController from '../controllers/ads.controller';
import uploadService from '../services/upload.service';

const router = new Router();

router.get('/api/materials', async ctx => { if (!ctx.state.principal) ctx.throw(401); ctx.body = await listMaterials(String(ctx.query.cursor || '')); });
router.post('/api/materials',async ctx=>{if(!ctx.state.principal)ctx.throw(401);if(!ctx.request.body||typeof ctx.request.body!=='object')ctx.throw(400);await mutate(ctx,async()=>({status:201,body:await createMaterial(ctx.request.body as any)}));});
router.post('/api/materials/upload', async (ctx,next)=>{if(!ctx.state.principal)ctx.throw(401);try{await uploadService.getPreparedUploadMiddleware()(ctx,next);}catch(e:any){ctx.throw(e.status||400,e.code==='LIMIT_FILE_SIZE'?'单个素材不能超过 100MB':e.message);}}, async ctx=>{const file=(ctx.request as any).file||(ctx as any).file;const result=uploadService.processUploadResult(file);if(!result.success)ctx.throw(400,result.error);ctx.body={...result.data,mime:file.mimetype};});
router.get('/api/materials/:id', async ctx => { if (!ctx.state.principal) ctx.throw(401); ctx.body=await getMaterial(ctx.params.id); });
router.put('/api/materials/:id', async ctx => { if (!ctx.state.principal) ctx.throw(401); if(!ctx.request.body || typeof ctx.request.body!=='object')ctx.throw(400); await mutate(ctx,async()=>({status:200,body:await changeMaterial(ctx.params.id,ctx.request.body as any)})); });
router.delete('/api/materials/:id', async ctx => { if (!ctx.state.principal) ctx.throw(401); await mutate(ctx,async()=>({status:200,body:await changeMaterial(ctx.params.id)})); });
router.get('/api/operations/:id', operationStatus);
router.post('/api/ads/search', async ctx => { ctx.body = await findAds(ctx.request.body, ctx.state.principal); });
router.get('/api/ads/page', async ctx => { if(!ctx.state.principal) ctx.throw(401); ctx.body = await listPage(ctx.query,ctx.state.principal); });
router.get('/api/ads/:id', async ctx => {
    if(!ctx.state.principal) ctx.throw(401);
    const rows = await withAdsConnection(async c => (await c.execute<RowDataPacket[]>(adProjection+' WHERE a.id=?',[ctx.params.id]))[0]);
    if(!rows.length) ctx.throw(404);
    const row=rows[0]; ctx.body={...row,price:Number(row.price),clicks:Number(row.clicks),videos:typeof row.videos==='string'?JSON.parse(row.videos):row.videos,images:typeof row.images==='string'?JSON.parse(row.images):row.images};
});
router.get('/api/ads', adsController.getAds);
router.post('/api/ads', adsController.createAd);
router.put('/api/ads/:id', adsController.updateAd);
router.delete('/api/ads/:id', adsController.deleteAd);
router.post('/api/ads/:id/click', adsController.clickAd);
router.post(
    '/api/upload',
    uploadService.getUploadMiddleware(),
    adsController.uploadVideo
);

export default router;
