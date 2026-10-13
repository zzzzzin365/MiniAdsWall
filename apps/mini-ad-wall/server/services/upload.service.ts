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

const preparedUpload = multer({ storage, limits: {fileSize:100*1024*1024}, fileFilter: (_req,file,cb)=>{
    const ext=path.extname(file.originalname).toLowerCase();
    const types:Record<string,string[]>={'.png':['image/png'],'.jpg':['image/jpeg'],'.jpeg':['image/jpeg'],'.webp':['image/webp'],'.mp4':['video/mp4'],'.mov':['video/quicktime'],'.webm':['video/webm']};
    if (!types[ext]?.includes(file.mimetype)) return cb(Object.assign(new Error('仅支持 JPG、PNG、WebP 图片和 MP4、MOV、WebM 视频'),{status:400}),false);
    cb(null,true);
}});
function getPreparedUploadMiddleware() { return preparedUpload.single('file'); }
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
    getUploadMiddleware, getPreparedUploadMiddleware,
    processUploadResult
};
