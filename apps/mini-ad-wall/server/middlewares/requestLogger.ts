import { Context, Next } from 'koa';

function getTimeString(): string {
    return new Date().toISOString().replace('T', ' ').substring(0, 19);
}

function getClientIP(ctx: Context): string {
    return ctx.request.ip ||
           (ctx.headers['x-forwarded-for'] as string) ||
           (ctx.headers['x-real-ip'] as string) ||
           'unknown';
}

async function requestLogger(ctx: Context, next: Next): Promise<void> {
    const requestId = Math.random().toString(36).substring(2, 10);
    const startTime = Date.now();

    console.log(`[${getTimeString()}] ← ${requestId} | ${ctx.method} ${ctx.url} | IP: ${getClientIP(ctx)}`);

    const body = (ctx.request as any).body as Record<string, any>;
    if (body && Object.keys(body).length > 0 && !ctx.url.includes('/upload')) {
        console.log(`[${getTimeString()}]   ${requestId} | Body:`, JSON.stringify(body).substring(0, 200));
    }

    await next();

    const duration = Date.now() - startTime;
    const status = ctx.status;
    const statusIcon = status >= 400 ? '✗' : '✓';

    console.log(`[${getTimeString()}] → ${requestId} | ${ctx.method} ${ctx.url} | ${statusIcon} ${status} | ${duration}ms`);
}

export default requestLogger;
