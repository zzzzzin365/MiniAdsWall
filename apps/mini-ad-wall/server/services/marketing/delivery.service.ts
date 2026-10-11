import { randomUUID } from 'crypto';
import { RowDataPacket } from 'mysql2/promise';
import { withMarketingConnection } from './clock';
import { resourceError } from './resource.service';
import { signToken, verifyToken, protocol } from './security';
import { ingest, serverTime, timeReason, writeEvent } from './ingestion';

export async function serve(campaignId: string, environment: string, visitorId: string) {
    return withMarketingConnection(async c => {
        await c.beginTransaction();
        try {
            const [activities] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_campaigns WHERE id=? AND environment=? FOR UPDATE', [campaignId, environment]);
            const now = await serverTime(c), activity = activities[0];
            if (!activity || activity.status !== 'active' || now < activity.starts_at || now >= activity.ends_at) throw resourceError('no_eligible_creative', 404, '当前没有可展示素材');
            const [rows] = await c.execute<RowDataPacket[]>(`SELECT v.*,a.ranking_score FROM marketing_creatives v JOIN marketing_ad_links l ON l.creative_id=v.id AND l.creative_version=v.version AND l.is_current=TRUE JOIN ads_business_ads a ON a.id=l.ad_id WHERE v.campaign_id=? AND v.environment=? AND v.status='approved' AND v.authorization_expires_at>? ORDER BY a.ranking_score DESC,a.id ASC LIMIT 1`, [campaignId, environment, now]);
            const creative = rows[0];
            if (!creative) throw resourceError('no_eligible_creative', 404, '当前没有可展示素材');
            const impression = randomUUID(), expires = new Date(now.getTime() + protocol.impressionSeconds * 1000);
            await c.execute(`INSERT INTO marketing_serving_decisions(impression_id,brand_id,environment,campaign_id,creative_id,creative_version,visitor_session_id,candidate_ref,policy_version,token_expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)`, [impression, activity.brand_id, environment, campaignId, creative.id, creative.version, visitorId, JSON.stringify({ creative_id: creative.id, score: creative.ranking_score }), 'legacy-score-v1', expires]);
            const token = signToken({ version: 1, kind: 'impression', environment, impression_id: impression, visitor_session_id: visitorId, exp: expires.getTime() });
            await c.commit();
            return { impression_id: impression, impression_token: token, click_url: '/api/marketing/click/' + token, asset_url: '/api/marketing/media/' + token, expires_at: expires.toISOString(), environment, creative: { id: creative.id, version: creative.version, title: creative.title, asset_ref: creative.asset_ref } };
        } catch (e) { await c.rollback(); throw e; }
    });
}
async function decision(c: any, claims: any, visitorId: string) {
    if (claims.visitor_session_id !== visitorId) throw resourceError('visitor_scope_mismatch', 403, '展示凭证不属于当前访问会话');
    const [r] = await c.execute(`SELECT d.*,v.landing_url,a.product_id FROM marketing_serving_decisions d JOIN marketing_creatives v ON v.id=d.creative_id AND v.version=d.creative_version JOIN marketing_campaigns a ON a.id=d.campaign_id WHERE d.impression_id=? AND d.environment=? FOR UPDATE`, [claims.impression_id, claims.environment]);
    if (!r[0] || r[0].visitor_session_id !== visitorId || r[0].token_expires_at.getTime() !== claims.exp) throw resourceError('invalid_decision', 403, '展示记录与凭证不一致');
    return r[0];
}
export async function impression(environment: string, visitorId: string, input: any) {
    const claims = verifyToken(input.impression_token, 'impression', environment);
    if (claims.visitor_session_id !== visitorId) throw resourceError('visitor_scope_mismatch', 403, '展示凭证不属于当前访问会话');
    return ingest('browser_' + environment, input.event_id, input, async c => {
        const row = await decision(c, claims, visitorId);
        const reason = timeReason(input.occurred_at, await serverTime(c));
        if (!reason) {
            const [existing] = await c.execute<RowDataPacket[]>("SELECT id FROM marketing_events WHERE environment=? AND valid_impression=?", [environment, row.impression_id]);
            if (existing[0]) return { status: 201, body: { receipt_id: existing[0].id, validation_status: 'valid', reason: null } };
        }
        const body = await writeEvent(c, { source: 'browser_' + environment, source_event_id: input.event_id, brand_id: row.brand_id, environment, type: 'impression', occurred_at: input.occurred_at, impression_id: row.impression_id, payload: { ...input, visitor_session_id: visitorId, campaign_id: row.campaign_id, creative_id: row.creative_id, creative_version: row.creative_version }, validation_status: reason ? 'quarantined' : 'valid', isolation_reason: reason });
        return { status: reason ? 202 : 201, body };
    });
}
export async function click(environment: string, visitorId: string, token: string) {
    const claims = verifyToken(token, 'impression', environment);
    if (claims.visitor_session_id !== visitorId) throw resourceError('visitor_scope_mismatch', 403, '展示凭证不属于当前访问会话');
    return ingest('click_' + environment, claims.impression_id, { token }, async c => {
        const row = await decision(c, claims, visitorId), now = await serverTime(c), id = randomUUID();
        const clickToken = signToken({ version: 1, kind: 'click', environment, click_id: id, impression_id: row.impression_id, visitor_session_id: visitorId, brand_id: row.brand_id, product_id: row.product_id, campaign_id: row.campaign_id, exp: now.getTime() + protocol.clickDays * 86400000 });
        const redirect = new URL(row.landing_url);
        redirect.searchParams.set('miniads_click', clickToken);
        await c.execute('INSERT INTO marketing_click_links(impression_id,click_id,environment,visitor_session_id,click_token,redirect_url,occurred_at) VALUES (?,?,?,?,?,?,?)', [row.impression_id, id, environment, visitorId, clickToken, redirect.toString(), now]);
        const receipt = await writeEvent(c, { source: 'click_' + environment, source_event_id: row.impression_id, brand_id: row.brand_id, environment, type: 'click', occurred_at: now.toISOString(), impression_id: row.impression_id, click_id: id, payload: { visitor_session_id: visitorId, campaign_id: row.campaign_id, creative_id: row.creative_id, creative_version: row.creative_version, product_id: row.product_id, click_id: id } });
        return { status: 302, body: { ...receipt, redirect_url: redirect.toString(), click_id: id } };
    });
}
export async function mediaReference(environment: string, visitorId: string, token: string) {
    const claims = verifyToken(token, 'impression', environment);
    return withMarketingConnection(async c => {
        const row = await decision(c, claims, visitorId);
        const [creative] = await c.execute<RowDataPacket[]>('SELECT asset_ref FROM marketing_creatives WHERE id=? AND version=?', [row.creative_id, row.creative_version]);
        return creative[0].asset_ref as string;
    });
}
