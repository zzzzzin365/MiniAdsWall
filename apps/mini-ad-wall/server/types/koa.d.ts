import { File } from '@koa/multer';

declare module 'koa' {
    interface Request {
        body?: any;
        file?: File;
        files?: File[];
    }

    interface DefaultContext {
        file?: File;
        files?: File[];
    }
}

export {};



