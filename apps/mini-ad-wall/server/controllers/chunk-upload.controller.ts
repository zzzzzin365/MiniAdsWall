import { RouterContext } from 'koa-router';
import chunkUploadService from '../services/chunk-upload.service';
import { ChunkCheckRequest, ChunkMergeRequest } from '../types';

async function checkFile(ctx: RouterContext): Promise<void> {
    const data = ctx.request.body as ChunkCheckRequest;

    if (!data.fileMd5 || !data.fileName || !data.totalChunks) {
        ctx.status = 400;
        ctx.body = { error: 'Missing fileMd5, fileName or totalChunks' };
        return;
    }

    const result = chunkUploadService.checkFile(data);
    ctx.body = result;
}

async function uploadChunk(ctx: RouterContext): Promise<void> {
    const file = (ctx.request as any).file || (ctx as any).file;
    const { fileMd5, chunkIndex, chunkMd5 } = ctx.request.body as any;

    if (!fileMd5 || chunkIndex === undefined || !chunkMd5) {
        ctx.status = 400;
        ctx.body = { error: 'Missing fileMd5, chunkIndex or chunkMd5' };
        return;
    }

    if (!file) {
        ctx.status = 400;
        ctx.body = { error: 'No chunk file uploaded' };
        return;
    }

    const chunkBuffer = require('fs').readFileSync(file.path);
    require('fs').unlinkSync(file.path);

    const result = chunkUploadService.saveChunk(
        fileMd5,
        parseInt(chunkIndex),
        chunkMd5,
        chunkBuffer
    );

    if (!result.success) {
        ctx.status = 400;
        ctx.body = { error: result.error };
        return;
    }

    ctx.body = { success: true, chunkIndex: parseInt(chunkIndex) };
}

async function mergeChunks(ctx: RouterContext): Promise<void> {
    const data = ctx.request.body as ChunkMergeRequest;

    if (!data.fileMd5 || !data.fileName || !data.totalChunks) {
        ctx.status = 400;
        ctx.body = { error: 'Missing fileMd5, fileName or totalChunks' };
        return;
    }

    const result = await chunkUploadService.mergeChunks(data);

    if (!result.success) {
        ctx.status = 400;
        ctx.body = { error: result.error };
        return;
    }

    ctx.body = {
        success: true,
        filename: result.filename,
        url: result.url
    };
}

export default {
    checkFile,
    uploadChunk,
    mergeChunks
};
