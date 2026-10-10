import { Context, Next } from 'koa';
import { RouterContext } from 'koa-router';
import { randomUUID, timingSafeEqual, createHash } from 'crypto';
import adsModel from '../models/ads.model';

function authenticated(ctx: Context): string | undefined {
    const entries: Record<string, string> = {};
    try { Object.assign(entries, JSON.parse(process.env.ADS_OPERATOR_TOKENS || '{}')); } catch { return; }
    if (process.env.ADS_OPERATOR_TOKEN) entries.operator = process.env.ADS_OPERATOR_TOKEN;
    const supplied = ctx.get('Authorization');
    for (const [subject, token] of Object.entries(entries)) {
        if (typeof token !== 'string' || !token || !subject || subject.length > 190) continue;
        const expected = `Bearer ${token}`;
        if (Buffer.byteLength(supplied) === Buffer.byteLength(expected) && timingSafeEqual(Buffer.from(supplied), Buffer.from(expected))) return subject;
    }
}

export async function businessBoundary(ctx: Context, next: Next) {
    const candidate = ctx.get('X-Request-ID');
    ctx.state.requestId = /^[a-zA-Z0-9_-]{1,100}$/.test(candidate) ? candidate : randomUUID();
    ctx.set('X-Request-ID', ctx.state.requestId);
    ctx.state.principal = authenticated(ctx);
    const mutation = ['POST', 'PUT', 'DELETE', 'PATCH'].includes(ctx.method)
        && ctx.path !== '/api/ads/search' && (/^\/api\/ads(?:\/[^/]+)?$/.test(ctx.path) || ctx.path.startsWith('/api/upload'));
    const operatorOnly = ctx.path === '/api/ads/search' || mutation || ctx.path.startsWith('/api/operations') || ctx.path.startsWith('/api/ai/') || ctx.path.startsWith('/api/agent/');
    if (operatorOnly && !ctx.state.principal) ctx.throw(401, '请提供有效的运营凭据');
    await next();
}

function canonical(value: any): string {
    if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
    if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
    return JSON.stringify(value);
}

export async function mutate(ctx: Context, apply: () => Promise<{ status: number; body: any }>) {
    const id = ctx.get('Idempotency-Key');
    if (!/^[a-zA-Z0-9_-]{16,100}$/.test(id)) ctx.throw(400, '缺少有效的 Idempotency-Key');
    const fingerprint = createHash('sha256').update(canonical({ method: ctx.method, path: ctx.path, body: ctx.request.body || null })).digest('hex');
    const result = await adsModel.executeOperation(ctx.state.principal, id, fingerprint, `${ctx.method} ${ctx.path}`, apply);
    ctx.status = result.status;
    ctx.body = result.body;
    ctx.set('X-Operation-ID', id);
    if(result.recall_receipt) ctx.set('X-Recall-Receipt', Buffer.from(JSON.stringify(result.recall_receipt)).toString('base64url'));
}

export async function operationStatus(ctx: RouterContext) {
    const record = await adsModel.getOperation(ctx.state.principal, ctx.params.id);
    if (!record) { ctx.status = 404; ctx.body = { state: 'not_found' }; return; }
    ctx.body = { state: 'completed', status: record.status, body: record.body, createdAt: record.createdAt, ...(record.recall_receipt ? {recall_receipt:record.recall_receipt} : {}) };
}
