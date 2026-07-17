import Koa from 'koa';
import adsRoutes from './ads.routes';
import formRoutes from './form.routes';
import aiRoutes from './ai.routes';
import chunkUploadRoutes from './chunk-upload.routes';

function registerRoutes(app: Koa): void {
    app.use(adsRoutes.routes() as unknown as Koa.Middleware);
    app.use(adsRoutes.allowedMethods() as unknown as Koa.Middleware);
    app.use(formRoutes.routes() as unknown as Koa.Middleware);
    app.use(formRoutes.allowedMethods() as unknown as Koa.Middleware);
    app.use(aiRoutes.routes() as unknown as Koa.Middleware);
    app.use(aiRoutes.allowedMethods() as unknown as Koa.Middleware);
    app.use(chunkUploadRoutes.routes() as unknown as Koa.Middleware);
    app.use(chunkUploadRoutes.allowedMethods() as unknown as Koa.Middleware);
}

export { registerRoutes };
