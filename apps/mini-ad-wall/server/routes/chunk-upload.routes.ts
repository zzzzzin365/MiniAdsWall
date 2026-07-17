import Router from 'koa-router';
import multer from '@koa/multer';
import path from 'path';
import config from '../config';
import chunkUploadController from '../controllers/chunk-upload.controller';

const router = new Router();

const chunkStorage = multer.diskStorage({
    destination: function (_req, _file, cb) {
        const tmpDir = path.join(config.UPLOAD_DIR, 'tmp');
        const fs = require('fs');
        if (!fs.existsSync(tmpDir)) {
            fs.mkdirSync(tmpDir, { recursive: true });
        }
        cb(null, tmpDir);
    },
    filename: function (_req, file, cb) {
        cb(null, `tmp_${Date.now()}_${Math.round(Math.random() * 1e9)}`);
    }
});

const chunkUpload = multer({ storage: chunkStorage });

router.post('/api/upload/check', chunkUploadController.checkFile);
router.post(
    '/api/upload/chunk',
    chunkUpload.single('chunk'),
    chunkUploadController.uploadChunk
);
router.post('/api/upload/merge', chunkUploadController.mergeChunks);

export default router;
