import { operationStatus } from '../middlewares/businessBoundary';
import Router from 'koa-router';
import adsController from '../controllers/ads.controller';
import uploadService from '../services/upload.service';

const router = new Router();

router.get('/api/operations/:id', operationStatus);
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
