import multer from '@koa/multer';
import path from 'path';
import fs from 'fs';
import config from '../config';
import { UploadResult } from '../types';

if (!fs.existsSync(config.UPLOAD_DIR)) {
    fs.mkdirSync(config.UPLOAD_DIR, { recursive: true });
}

const storage = multer.diskStorage({
    destination: function (_req, _file, cb) {
        cb(null, config.UPLOAD_DIR);
    },
    filename: function (_req, file, cb) {
        const uniqueSuffix = Date.now() + '-' + Math.round(Math.random() * 1E9);
        cb(null, uniqueSuffix + path.extname(file.originalname));
    }
});

const upload = multer({ storage: storage });

function getUploadMiddleware() {
    return upload.single('video');
}

function processUploadResult(file: Express.Multer.File | undefined): UploadResult {
    if (!file) {
        return {
            success: false,
            error: 'No file uploaded'
        };
    }

    return {
        success: true,
        data: {
            filename: file.filename,
            url: `http://localhost:${config.PORT}/${file.filename}`
        }
    };
}

export default {
    getUploadMiddleware,
    processUploadResult
};
