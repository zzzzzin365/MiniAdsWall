import { RouterContext } from 'koa-router';
import { mutate } from '../middlewares/businessBoundary';
import adsService from '../services/ads.service';
import uploadService from '../services/upload.service';

async function getAds(ctx: RouterContext): Promise<void> {
    const ads = adsService.getSortedAds();
    ctx.body = ads;
}

async function createAd(ctx: RouterContext): Promise<void> {
    mutate(ctx, () => {
        const result = adsService.createAd(ctx.request.body as any);
        return { status: result.success ? 201 : 400, body: result.success ? result.data : { error: result.error } };
    });
}

async function updateAd(ctx: RouterContext): Promise<void> {
    mutate(ctx, () => {
        const result = adsService.updateAd(ctx.params.id, ctx.request.body as any);
        return { status: result.success ? 200 : result.error === 'Ad not found' ? 404 : 400, body: result.success ? result.data : { error: result.error } };
    });
}

async function deleteAd(ctx: RouterContext): Promise<void> {
    mutate(ctx, () => {
        const result = adsService.deleteAd(ctx.params.id);
        return { status: result.success ? 204 : 404, body: result.success ? null : { error: result.error } };
    });
}

async function clickAd(ctx: RouterContext): Promise<void> {
    const { id } = ctx.params;
    const result = adsService.clickAd(id);

    if (!result.success) {
        ctx.status = 404;
        ctx.body = { error: result.error };
        return;
    }

    ctx.body = { clicks: result.clicks };
}

async function uploadVideo(ctx: RouterContext): Promise<void> {
    const file = (ctx.request as any).file || (ctx as any).file;
    const result = uploadService.processUploadResult(file);

    if (!result.success) {
        ctx.status = 400;
        ctx.body = { error: result.error };
        return;
    }

    ctx.body = result.data;
}

export default {
    getAds,
    createAd,
    updateAd,
    deleteAd,
    clickAd,
    uploadVideo
};
