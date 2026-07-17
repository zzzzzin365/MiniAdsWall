import { RouterContext } from 'koa-router';
import adsService from '../services/ads.service';
import uploadService from '../services/upload.service';

async function getAds(ctx: RouterContext): Promise<void> {
    const ads = adsService.getSortedAds();
    ctx.body = ads;
}

async function createAd(ctx: RouterContext): Promise<void> {
    const data = ctx.request.body as any;
    const result = adsService.createAd(data);

    if (!result.success) {
        ctx.status = 400;
        ctx.body = { error: result.error };
        return;
    }

    ctx.body = result.data;
}

async function updateAd(ctx: RouterContext): Promise<void> {
    const { id } = ctx.params;
    const data = ctx.request.body as any;
    const result = adsService.updateAd(id, data);

    if (!result.success) {
        ctx.status = 404;
        ctx.body = { error: result.error };
        return;
    }

    ctx.body = result.data;
}

async function deleteAd(ctx: RouterContext): Promise<void> {
    const { id } = ctx.params;
    const result = adsService.deleteAd(id);

    if (!result.success) {
        ctx.status = 404;
        ctx.body = { error: result.error };
        return;
    }

    ctx.status = 204;
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
