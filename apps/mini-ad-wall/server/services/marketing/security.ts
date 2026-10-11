import { createHmac, randomUUID, timingSafeEqual } from 'crypto';
import { Context } from 'koa';
import { loadMarketing } from '../../config/marketing';
import { resourceError } from './resource.service';

export const protocol = Object.freeze({ impressionSeconds: 600, visitorDays: 30, clickDays: 37, futureMinutes: 5, lateDays: 30 });
export type Source = { name: string; token: string; environment: string; brand_ids: string[]; interfaces: string[] };
export const sourceKey = (source: Source) => source.name + '_' + source.environment;
function key() {
    const value = process.env.MARKETING_SIGNING_KEY || '';
    if (Buffer.byteLength(value) < 32) throw resourceError('signing_not_configured', 503, '营销签名密钥尚未配置');
    return value;
}
export function signToken(claims: any) {
    const payload = Buffer.from(JSON.stringify(claims)).toString('base64url');
    return payload + '.' + createHmac('sha256', key()).update(payload).digest('base64url');
}
export function verifyToken(token: string, kind: string, environment: string) {
    if (typeof token !== 'string' || token.length > 4096) throw resourceError('invalid_token', 400, '凭证格式错误');
    const parts = token.split('.');
    if (parts.length !== 2 || !parts.every(v => /^[A-Za-z0-9_-]+$/.test(v))) throw resourceError('invalid_token', 400, '凭证格式错误');
    const expected = createHmac('sha256', key()).update(parts[0]).digest();
    const supplied = Buffer.from(parts[1], 'base64url');
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) throw resourceError('invalid_token', 403, '凭证签名错误');
    let claims: any;
    try { claims = JSON.parse(Buffer.from(parts[0], 'base64url').toString('utf8')); } catch { throw resourceError('invalid_token', 400, '凭证内容错误'); }
    if (!claims || claims.kind !== kind || claims.environment !== environment || claims.version !== 1 || !Number.isSafeInteger(claims.exp)) throw resourceError('token_scope_mismatch', 403, '凭证用途或环境不匹配');
    if (claims.exp <= Date.now()) throw resourceError('token_expired', 410, '凭证已过期，请重新获取展示机会');
    return claims;
}
export function visitor(ctx: Context, environment: string, create = false) {
    const token = ctx.cookies.get('marketing_visitor', { signed: false });
    if (token) {
        try {
            const claims = verifyToken(token, 'visitor', environment);
            if (/^[A-Za-z0-9_-]{1,100}$/.test(claims.id)) return claims.id as string;
        } catch (error) { if (!create) throw error; }
    }
    if (!create) throw resourceError('visitor_session_required', 401, '请先获取展示机会');
    const id = randomUUID();
    const claims = { version: 1, kind: 'visitor', environment, id, exp: Date.now() + protocol.visitorDays * 86400000 };
    ctx.cookies.set('marketing_visitor', signToken(claims), { signed: false, httpOnly: true, sameSite: 'lax', secure: ctx.secure, maxAge: protocol.visitorDays * 86400000, overwrite: true });
    return id;
}
export function sourcePolicies(): Source[] {
    let raw: any;
    try { raw = JSON.parse(process.env.MARKETING_SOURCES || '{}'); } catch { throw resourceError('source_configuration_invalid', 503, '营销来源配置无效'); }
    if (!raw || Array.isArray(raw) || typeof raw !== 'object') throw resourceError('source_configuration_invalid', 503, '营销来源配置无效');
    const sources: Source[] = Object.entries(raw).map(([name, value]: [string, any]) => {
        if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(k => !['token', 'environment', 'brand_ids', 'interfaces'].includes(k))) throw resourceError('source_configuration_invalid', 503, '营销来源配置无效');
        return { ...value, name };
    });
    const allowed = ['conversions', 'spend-entries', 'spend-coverage'];
    const tokens = new Set<string>();
    let operator: any = {};
    try { operator = JSON.parse(process.env.ADS_OPERATOR_TOKENS || '{}'); } catch { /* business auth fails separately */ }
    const operatorTokens = [...Object.values(operator || {}), process.env.ADS_OPERATOR_TOKEN];
    for (const s of sources) {
        if (!/^[A-Za-z0-9_-]{1,60}$/.test(s.name) || typeof s.token !== 'string' || s.token.length < 24 || tokens.has(s.token) || operatorTokens.includes(s.token) || !['synthetic', 'sandbox', 'production'].includes(s.environment) || !Array.isArray(s.brand_ids) || !s.brand_ids.length || s.brand_ids.some(id => typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(id)) || !Array.isArray(s.interfaces) || s.interfaces.some(v => !allowed.includes(v))) throw resourceError('source_configuration_invalid', 503, '营销来源配置无效');
        tokens.add(s.token);
    }
    return sources;
}
export function authenticateSource(ctx: Context, endpoint: string) {
    const supplied = Buffer.from(ctx.get('Authorization'));
    const source = sourcePolicies().find(s => {
        const expected = Buffer.from('Bearer ' + s.token);
        return supplied.length === expected.length && timingSafeEqual(supplied, expected);
    });
    if (!source) throw resourceError('source_authentication_required', 401, '需要数据接入方凭据');
    if (!source.interfaces.includes(endpoint) || source.environment !== loadMarketing().environment) throw resourceError('source_forbidden', 403, '数据来源没有该接口或环境权限');
    return source;
}
