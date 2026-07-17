import { Context, Next } from 'koa';

interface HttpError extends Error {
    status?: number;
    statusCode?: number;
}

async function errorHandler(ctx: Context, next: Next): Promise<void> {
    try {
        await next();
    } catch (err) {
        const error = err as HttpError;
        console.error('Server Error:', error);
        ctx.status = error.status || error.statusCode || 500;
        ctx.body = {
            error: error.message || 'Internal Server Error'
        };
        ctx.app.emit('error', error, ctx);
    }
}

export default errorHandler;
