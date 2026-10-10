import { randomUUID } from 'crypto';
import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { currentAdsConnection, recordOperationChange } from '../../models/ads.model';
import { withAdsConnection } from '../ads.database';
import { CreateCampaign, CreateCreative, CampaignStatus, CreativeStatus } from '../../types/marketing';

export const resourceError = (code: string, status: number, message: string) => Object.assign(new Error(message), { code, status });
const iso = (row: RowDataPacket) => Object.fromEntries(Object.entries(row).map(([k, v]) => [k, v instanceof Date ? v.toISOString() : v]));
async function authorize(c: PoolConnection, principal: string, brand: string, environment: string, write = false) {
    const [r] = await c.execute<RowDataPacket[]>(`SELECT b.id,m.role FROM marketing_brands b JOIN marketing_memberships m ON m.brand_id=b.id WHERE b.id=? AND b.environment=? AND m.principal=?`, [brand, environment, principal]);
    if (!r.length || (write && !r.some(v => v.role === 'operator'))) throw resourceError('resource_forbidden', 403, '没有该品牌资源的操作权限');
}
async function campaign(c: PoolConnection, id: string, environment: string, lock = false) {
    const [r] = await c.execute<RowDataPacket[]>(`SELECT * FROM marketing_campaigns WHERE id=? AND environment=?${lock ? ' FOR UPDATE' : ''}`, [id, environment]);
    if (!r.length) throw resourceError('resource_not_found', 404, '活动不存在');
    return r[0];
}
async function creative(c: PoolConnection, id: string, environment: string, lock = false) {
    const [r] = await c.execute<RowDataPacket[]>(`SELECT * FROM marketing_creatives WHERE id=? AND environment=? ORDER BY version DESC LIMIT 1${lock ? ' FOR UPDATE' : ''}`, [id, environment]);
    if (!r.length) throw resourceError('resource_not_found', 404, '素材不存在');
    return r[0];
}

export async function createCampaign(principal: string, environment: string, input: CreateCampaign) {
    const c = currentAdsConnection();
    await authorize(c, principal, input.brand_id, environment, true);
    if (Date.parse(input.starts_at) >= Date.parse(input.ends_at)) throw resourceError('invalid_time_range', 422, '活动开始时间必须早于结束时间');
    const [p] = await c.execute<RowDataPacket[]>('SELECT id FROM marketing_products WHERE id=? AND brand_id=? AND environment=?', [input.product_id, input.brand_id, environment]);
    if (!p.length) throw resourceError('product_scope_mismatch', 422, '商品不属于该品牌或环境');
    const id = randomUUID();
    await c.execute(`INSERT INTO marketing_campaigns(id,brand_id,environment,product_id,name,objective,starts_at,ends_at,created_by) VALUES (?,?,?,?,?,?,?,?,?)`, [id, input.brand_id, environment, input.product_id, input.name, input.objective, new Date(input.starts_at), new Date(input.ends_at), principal]);
    const result = iso(await campaign(c, id, environment));
    recordOperationChange(null, result);
    return result;
}

export async function createCreative(principal: string, environment: string, input: CreateCreative) {
    const c = currentAdsConnection();
    await authorize(c, principal, input.brand_id, environment, true);
    const activity = await campaign(c, input.campaign_id, environment, true);
    if (activity.brand_id !== input.brand_id || activity.status === 'ended') throw resourceError('campaign_scope_mismatch', 422, '活动品牌不匹配或已结束');
    const [owners] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_ad_owners WHERE ad_id=? AND brand_id=? AND environment=? FOR UPDATE', [input.ad_id, input.brand_id, environment]);
    if (!owners.length) throw resourceError('ad_scope_mismatch', 403, '旧广告尚未分配给该品牌');
    const [ads] = await c.execute<RowDataPacket[]>('SELECT id FROM ads_business_ads WHERE id=? FOR UPDATE', [input.ad_id]);
    if (!ads.length) throw resourceError('ad_not_found', 404, '旧广告不存在');
    if (input.asset_ref.split(/[\\/]/).some((v: string) => v === '..')) throw resourceError('invalid_asset_reference', 422, '素材引用包含非法路径');
    const [clock] = await c.query<RowDataPacket[]>('SELECT UTC_TIMESTAMP(3) AS now');
    if (new Date(input.authorization_expires_at).getTime() <= clock[0].now.getTime()) throw resourceError('authorization_expired', 422, '素材授权已过期');
    const id = randomUUID();
    await c.execute(`INSERT INTO marketing_creatives(id,brand_id,environment,campaign_id,ad_id,title,asset_ref,asset_hash,landing_url,authorization_ref,authorization_expires_at,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, [id, input.brand_id, environment, input.campaign_id, input.ad_id, input.title, input.asset_ref, input.asset_hash, input.landing_url, input.authorization_ref, new Date(input.authorization_expires_at), principal]);
    // Serialize bindings on the owner row; retain historical link revisions.
    // Never replace an active link with a draft while its campaign is running.
    const [links] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_ad_links WHERE ad_id=? ORDER BY revision DESC LIMIT 1', [input.ad_id]);
    if (links.length) {
        const linked = await campaign(c, links[0].campaign_id, environment, true);
        if (linked.status === 'active') throw resourceError('active_ad_link', 409, '广告正在活动中使用，请先暂停原活动再绑定新素材');
        await c.execute('UPDATE marketing_ad_links SET is_current=FALSE WHERE ad_id=? AND is_current=TRUE', [input.ad_id]);
    }
    await c.execute(`INSERT INTO marketing_ad_links(ad_id,revision,brand_id,environment,campaign_id,creative_id,creative_version) VALUES (?,?,?,?,?,?,1)`, [input.ad_id, (links[0]?.revision || 0) + 1, input.brand_id, environment, input.campaign_id, id]);
    const result = iso(await creative(c, id, environment));
    recordOperationChange(null, result);
    return result;
}

export async function changeCampaignStatus(principal: string, environment: string, id: string, input: CampaignStatus) {
    const c = currentAdsConnection(), before = await campaign(c, id, environment, true);
    await authorize(c, principal, before.brand_id, environment, true);
    if (before.version !== input.expected_version) throw resourceError('version_conflict', 409, '活动版本已变化');
    const allowed: Record<string, string[]> = { draft: ['active', 'ended'], active: ['paused', 'ended'], paused: ['active', 'ended'], ended: [] };
    if (!allowed[before.status].includes(input.status)) throw resourceError('invalid_state_transition', 422, '不允许该活动状态变更');
    if (input.status === 'active') {
        const [ready] = await c.query<RowDataPacket[]>(`SELECT COUNT(*) AS total FROM marketing_creatives v JOIN marketing_ad_links l ON l.creative_id=v.id AND l.creative_version=v.version AND l.is_current=TRUE JOIN ads_business_ads a ON a.id=l.ad_id WHERE v.campaign_id=? AND v.status='approved' AND v.authorization_expires_at>UTC_TIMESTAMP(3)`, [id]);
        const [clock] = await c.query<RowDataPacket[]>('SELECT UTC_TIMESTAMP(3) AS now');
        if (Number(ready[0].total) < 1 || clock[0].now < before.starts_at || clock[0].now >= before.ends_at) throw resourceError('campaign_not_ready', 422, '活动时间或已审核素材不满足发布条件');
    }
    await c.execute('UPDATE marketing_campaigns SET status=?,version=version+1 WHERE id=?', [input.status, id]);
    const result = iso(await campaign(c, id, environment));
    recordOperationChange(iso(before), result);
    return result;
}

export async function changeCreativeStatus(principal: string, environment: string, id: string, input: CreativeStatus) {
    const c = currentAdsConnection();
    const preview = await creative(c, id, environment);
    // Use the same campaign -> creative order as campaign publication.
    const activity = await campaign(c, preview.campaign_id, environment, true);
    const before = await creative(c, id, environment, true);
    await authorize(c, principal, before.brand_id, environment, true);
    if (before.state_revision !== input.expected_revision) throw resourceError('version_conflict', 409, '素材状态版本已变化');
    const allowed: Record<string, string[]> = { draft: ['pending_review', 'archived'], pending_review: ['approved', 'rejected', 'archived'], approved: ['archived'], rejected: ['pending_review', 'archived'], archived: [] };
    if (!allowed[before.status].includes(input.status)) throw resourceError('invalid_state_transition', 422, '不允许该素材状态变更');
    if (activity.status === 'ended' && input.status !== 'archived') throw resourceError('campaign_ended', 422, '活动已结束');
    if (input.status === 'approved') {
        const [clock] = await c.query<RowDataPacket[]>('SELECT UTC_TIMESTAMP(3) AS now');
        if (before.authorization_expires_at <= clock[0].now) throw resourceError('authorization_expired', 422, '素材授权已过期');
    }
    await c.execute(`UPDATE marketing_creatives SET status=?,state_revision=state_revision+1,reviewer=?,reviewed_at=? WHERE id=? AND version=?`, [input.status, input.status === 'approved' || input.status === 'rejected' ? principal : before.reviewer, input.status === 'approved' || input.status === 'rejected' ? new Date() : before.reviewed_at, id, before.version]);
    const result = iso(await creative(c, id, environment));
    recordOperationChange(iso(before), result);
    return result;
}

export async function getResource(principal: string, environment: string, kind: 'campaigns' | 'creatives', id: string) {
    return withAdsConnection(async c => {
        const row = kind === 'campaigns' ? await campaign(c, id, environment) : await creative(c, id, environment);
        await authorize(c, principal, row.brand_id, environment);
        return iso(row);
    });
}
export async function listResources(principal: string, environment: string, kind: 'campaigns' | 'creatives', brand: string, cursor = '', limit = 50) {
    return withAdsConnection(async c => {
        await authorize(c, principal, brand, environment);
        const [rows] = await c.query<RowDataPacket[]>(`SELECT r.* FROM marketing_${kind} r WHERE r.brand_id=? AND r.environment=? AND r.id>? ORDER BY r.id LIMIT ?`, [brand, environment, cursor, limit + 1]);
        const items = rows.slice(0, limit).map(iso);
        return { items, next_cursor: rows.length > limit ? items[items.length - 1].id : null };
    });
}
