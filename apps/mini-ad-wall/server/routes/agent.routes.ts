import Router from 'koa-router';
import { Readable, Transform } from 'stream';
import { adContext } from '../services/recall/search';
import adsModel from '../models/ads.model';
import config from '../config';

const router = new Router({ prefix: '/api/agent' });
const logins = new Map<string, { token: string; user_id: string; workspace_id: string; expires: number }>();
const base = () => (process.env.AGENT_HOSTING_URL || 'http://127.0.0.1:8002').replace(/\/$/, '');
const internalHeaders = () => ({ Authorization: `Bearer ${process.env.AGENT_SERVICE_TOKEN || ''}` });

async function login(subject: string) {
    const existing = logins.get(subject);
    if (existing && existing.expires > Date.now()) return existing;
    const response = await fetch(`${base()}/auth/session`, {
        method: 'POST', headers: { ...internalHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject }), signal: AbortSignal.timeout(5000)
    });
    if (!response.ok) throw new Error('Agent 登录服务不可用');
    const data = await response.json() as any;
    const session = { token: data.session_token, user_id: data.user_id, workspace_id: data.workspace_id, expires: Date.now() + 3600000 };
    // This is just a gateway token cache; the authority and expiration live in Redis.
    if (logins.size >= 10000) logins.delete(logins.keys().next().value!);
    logins.set(subject, session);
    return session;
}

router.use(async (ctx, next) => {
    if (!ctx.state.principal) ctx.throw(401, '请提供有效的运营凭据');
    await next();
});
router.get('/identity', async ctx => {
    const identity = await login(ctx.state.principal);
    ctx.body = { user_id: identity.user_id, workspace_id: identity.workspace_id };
});

router.all('(.*)', async ctx => {
    const path = ctx.path.slice('/api/agent'.length);
    const routes: Record<string, RegExp[]> = {
        GET: [/^\/sessions$/, /^\/sessions\/\d+\/(history|messages|tool-calls)$/, /^\/runs\/\d+$/, /^\/runs\/\d+\/events$/, /^\/runs\/\d+\/objects\/[a-f0-9]{64}$/],
        POST: [/^\/sessions$/, /^\/sessions\/\d+\/runs$/, /^\/runs\/\d+\/(cancel|resume)$/, /^\/approvals\/\d+\/decision$/]
    };
    if (!routes[ctx.method]?.some(pattern => pattern.test(path))) ctx.throw(404);
    let session = await login(ctx.state.principal);
    let body = ctx.request.body;
    if (/^\/sessions\/\d+\/runs$/.test(path) && ctx.method === 'POST') {
        // The Agent sees the authenticated server snapshot, never browser-forged ad facts.
        if (config.RELIABILITY.features.AD_CONTEXT_V1_ENABLED) {
            const context = await adContext(body.conditions,ctx.state.principal);
            body = { ...body, conditions:body.conditions||[], ads:context.items };
        delete body.ad_context;
        if(['true','1'].includes(process.env.AD_CONTEXT_V1_ENABLED||'')) body.ad_context=context;
        } else {
            if(body.conditions?.length) ctx.throw(503,'feature_disabled');
            const context = await adContext([],ctx.state.principal);
            body = { ...body, ads:context.items };
            delete body.ad_context;
        }
        delete body.user_id; delete body.userId;
    }
    const stream = path.endsWith('/events');
    const controller = new AbortController();
    const stop = () => controller.abort();
    ctx.res.once('close', stop);
    const timer = setTimeout(stop, stream ? 15 * 60000 : 15000);
    const send = () => fetch(`${base()}${path}${ctx.querystring ? '?' + ctx.querystring : ''}`, {
        method: ctx.method, signal: controller.signal,
        headers: { ...internalHeaders(), 'Content-Type': 'application/json', 'X-Agent-Session': session.token,
            'Idempotency-Key': ctx.get('Idempotency-Key'), 'Last-Event-ID': ctx.get('Last-Event-ID') || '0' },
        body: ctx.method === 'GET' ? undefined : JSON.stringify(body || {})
    });
    try {
        let upstream = await send();
        if (upstream.status === 401) {
            await upstream.body?.cancel(); logins.delete(ctx.state.principal);
            session = await login(ctx.state.principal); upstream = await send();
        }
        ctx.status = upstream.status;
        if (stream && upstream.ok && upstream.body) {
            ctx.set('Content-Type', 'text/event-stream');
            ctx.set('Cache-Control', 'no-cache, no-transform'); ctx.set('X-Accel-Buffering', 'no');
            // Node backpressure prevents unbounded buffering; an unresponsive client is disconnected.
            const source = Readable.fromWeb(upstream.body as any, { highWaterMark: 64 * 1024 });
            const pass = new Transform({ highWaterMark: 64 * 1024, transform(chunk, encoding, done) { done(null, chunk); } });
            source.on('error', error => pass.destroy(error));
            const idle = setInterval(() => {
                if (pass.readableLength + pass.writableLength + source.readableLength > 256 * 1024) {
                    source.destroy(); pass.destroy(); stop();
                }
            }, 1000);
            const cleanup = () => { clearInterval(idle); clearTimeout(timer); stop(); source.destroy(); };
            pass.once('close', cleanup); pass.once('end', cleanup);
            ctx.body = source.pipe(pass);
        } else if (path.includes('/objects/') && upstream.ok) {
            ctx.set('Content-Type', 'application/octet-stream'); ctx.set('Content-Disposition', 'attachment');
            ctx.body = Readable.fromWeb(upstream.body as any);
            ctx.body.once('close', () => { clearTimeout(timer); stop(); });
        } else {
            ctx.body = await upstream.json(); clearTimeout(timer); ctx.res.removeListener('close', stop);
            if (upstream.headers.get('Retry-After')) ctx.set('Retry-After', upstream.headers.get('Retry-After')!);
        }
    } catch (error) {
        clearTimeout(timer); stop(); ctx.throw(503, 'Agent 托管服务不可用');
    }
});
export default router;
