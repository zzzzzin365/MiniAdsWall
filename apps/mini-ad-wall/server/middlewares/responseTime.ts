import { Context, Next } from 'koa';

async function responseTime(ctx: Context, next: Next): Promise<void> {
    const start = Date.now();
    await next();
    const duration = Date.now() - start;
    ctx.set('X-Response-Time', `${duration}ms`);
    if (duration > 1000) {
        console.warn(`[SLOW REQUEST] ${ctx.method} ${ctx.url} took ${duration}ms`);
    }
}

export default responseTime;
