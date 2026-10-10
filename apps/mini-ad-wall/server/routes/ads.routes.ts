import { operationStatus } from '../middlewares/businessBoundary';
import { findAds, listPage } from '../services/recall/search';
import { withAdsConnection } from '../services/ads.database';
import { RowDataPacket } from 'mysql2/promise';
import Router from 'koa-router';
import adsController from '../controllers/ads.controller';
import uploadService from '../services/upload.service';

const router = new Router();

router.get('/api/operations/:id', operationStatus);
router.post('/api/ads/search', async ctx => { ctx.body = await findAds(ctx.request.body, ctx.state.principal); });
router.get('/api/ads/page', async ctx => { if(!ctx.state.principal) ctx.throw(401); ctx.body = await listPage(ctx.query,ctx.state.principal); });
router.get('/api/ads/:id', async ctx => {
    if(!ctx.state.principal) ctx.throw(401);
    const rows = await withAdsConnection(async c => (await c.execute<RowDataPacket[]>('SELECT id,title,publisher,content,url,price,clicks,videos,version FROM ads_business_ads WHERE id=?',[ctx.params.id]))[0]);
    if(!rows.length) ctx.throw(404);
    const row=rows[0]; ctx.body={...row,price:Number(row.price),clicks:Number(row.clicks),videos:typeof row.videos==='string'?JSON.parse(row.videos):row.videos};
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
