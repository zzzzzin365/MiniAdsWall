import { randomUUID } from 'crypto';
import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { withMarketingConnection } from './clock';
import { requireContract } from './contracts';
import { hash } from './ingestion';
import { calculateMetrics, Totals } from './metrics';
import { resourceError } from './resource.service';
import { sourcePolicies } from './security';

export type ReportQuery = {
    brand_id: string; campaign_id: string; environment: string; start: string; end: string;
    timezone: 'Asia/Shanghai' | 'UTC'; rule_version: 'last-click-v1';
    creative_id?: string; creative_version?: number;
};
const DAY = 86400000;
const safe = (v: any) => {
    const n = Number(v ?? 0);
    if (!Number.isSafeInteger(n) || n < 0) throw resourceError('metric_out_of_range', 422, '指标超出精确计算范围');
    return n;
};
const iso = (v: any) => v ? new Date(v).toISOString() : null;
const decoded = (v: any) => typeof v === 'string' ? JSON.parse(v) : v;
async function rows(c: PoolConnection, sql: string, params: any[]) {
    const [result] = await c.execute<RowDataPacket[]>('SELECT /*+ MAX_EXECUTION_TIME(3000) */ ' + sql, params);
    return result;
}
async function authorize(c: PoolConnection, principal: string, brand: string, environment: string) {
    const membership = await rows(c, 'b.id FROM marketing_brands b JOIN marketing_memberships m ON m.brand_id=b.id WHERE b.id=? AND b.environment=? AND m.principal=? LIMIT 1', [brand, environment, principal]);
    if (!membership.length) throw resourceError('resource_forbidden', 403, '没有该品牌报表的访问权限');
}
export function parseReportQuery(raw: any, environment: string): ReportQuery {
    const input = { ...raw };
    if (input.creative_version !== undefined && typeof input.creative_version === 'string' && /^\d+$/.test(input.creative_version)) input.creative_version = Number(input.creative_version);
    requireContract('ReportQuery', input);
    if (input.environment !== environment) throw resourceError('report_environment_mismatch', 403, '报表环境与当前服务不匹配');
    const span = Date.parse(input.end) - Date.parse(input.start);
    if (span <= 0) throw resourceError('invalid_interval', 400, '结束时间必须晚于开始时间');
    if (span > 31 * DAY) throw resourceError('report_window_limit', 422, '单次报表最多查询31天，请分段查询');
    return input;
}

// A complete interval must be reconciled by EVERY frozen spend source. A broad
// marker may cover the requested subinterval; adjacent complete markers may join.
export function intervalCovered(start: number, end: number, markers: { starts_at: any; ends_at: any; status: string }[]) {
    if (markers.some(m => m.status !== 'complete' && new Date(m.starts_at).getTime() < end && new Date(m.ends_at).getTime() > start)) return false;
    let cursor = start;
    for (const m of [...markers].filter(m => m.status === 'complete').sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime())) {
        const left = new Date(m.starts_at).getTime(), right = new Date(m.ends_at).getTime();
        if (right <= cursor) continue;
        if (left > cursor) break;
        cursor = right;
        if (cursor >= end) return true;
    }
    return false;
}

export async function createReport(principal: string, q: ReportQuery) {
    return withMarketingConnection(async c => {
        await c.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');
        await c.query('START TRANSACTION WITH CONSISTENT SNAPSHOT');
        try {
            await authorize(c, principal, q.brand_id, q.environment);
            const activities = await rows(c, 'id FROM marketing_campaigns WHERE id=? AND brand_id=? AND environment=?', [q.campaign_id, q.brand_id, q.environment]);
            if (!activities.length) throw resourceError('resource_not_found', 404, '该品牌没有此活动');
            if (q.creative_id) {
                const materials = await rows(c, 'id FROM marketing_creatives WHERE id=? AND version=? AND campaign_id=? AND brand_id=? AND environment=?', [q.creative_id, q.creative_version, q.campaign_id, q.brand_id, q.environment]);
                if (!materials.length) throw resourceError('creative_scope_mismatch', 422, '素材版本不属于该活动');
            }
            const clock = await rows(c, 'UTC_TIMESTAMP(3) AS now', []), now = clock[0].now as Date;
            const state = (await rows(c, 's.* FROM marketing_aggregation_state s WHERE brand_id=? AND environment=? AND rule_version=?', [q.brand_id, q.environment, q.rule_version]))[0];
            const input = (await rows(c, 'epoch FROM marketing_input_epochs WHERE brand_id=? AND environment=?', [q.brand_id, q.environment]))[0];
            const current = !input ? true : !!state && BigInt(state.input_epoch) === BigInt(input.epoch);
            const scope = [q.brand_id, q.environment, q.campaign_id];
            const suffix = q.creative_id ? ' AND d.creative_id=? AND d.creative_version=?' : '';
            const creative = q.creative_id ? [q.creative_id, q.creative_version] : [];
            const counts = await rows(c, `e.type,v.status,COUNT(*) AS n FROM marketing_events e
                JOIN marketing_event_validation v ON v.event_id=e.id AND v.revision=(SELECT MAX(v2.revision) FROM marketing_event_validation v2 WHERE v2.event_id=e.id)
                JOIN marketing_serving_decisions d ON d.impression_id=e.impression_id AND d.environment=e.environment AND d.brand_id=e.brand_id
                WHERE e.brand_id=? AND e.environment=? AND d.campaign_id=? AND e.occurred_at>=? AND e.occurred_at<?${suffix}
                GROUP BY e.type,v.status`, [...scope, new Date(q.start), new Date(q.end), ...creative]);
            const count = (type: string) => safe(counts.find(r => r.type === type && r.status === 'valid')?.n);
            const orderSuffix = q.creative_id ? ' AND creative_id=? AND creative_version=?' : '';
            const orderScope = [...scope, q.rule_version, new Date(q.start), new Date(q.end), ...creative];
            const orders = (await rows(c, `COUNT(*) AS paid_orders,COALESCE(SUM(net_revenue_minor),0) AS revenue_minor,COALESCE(SUM(refunded_minor>0),0) AS refunded_orders FROM marketing_click_orders
                WHERE brand_id=? AND environment=? AND campaign_id=? AND rule_version=? AND paid_at>=? AND paid_at<?${orderSuffix}`, orderScope))[0];
            const cohort = (await rows(c, `COUNT(*) AS n FROM marketing_click_orders WHERE brand_id=? AND environment=? AND campaign_id=? AND rule_version=? AND click_at>=? AND click_at<?${orderSuffix}`, orderScope))[0];
            const spent = (await rows(c, `COALESCE(SUM(amount_minor),0) AS n FROM marketing_spend_ledger WHERE brand_id=? AND environment=? AND campaign_id=? AND occurred_at>=? AND occurred_at<?${orderSuffix}`, [...scope, new Date(q.start), new Date(q.end), ...creative]))[0];
            const policy = await rows(c, 'source FROM marketing_source_policies WHERE campaign_id=? AND environment=? ORDER BY source', [q.campaign_id, q.environment]);
            const configured = sourcePolicies().filter(s => s.environment === q.environment && s.brand_ids.includes(q.brand_id) && s.interfaces.includes('spend-entries')).map(s => s.name).sort();
            const expected = policy.length ? policy.map(p => p.source as string) : configured;
            const policyChanged = hash(expected) !== hash(configured);
            const markers = await rows(c, `a.source,a.starts_at,a.ends_at,a.revision,a.status,a.reason FROM marketing_source_coverage a
                WHERE a.campaign_id=? AND a.environment=? AND a.starts_at<? AND a.ends_at>? AND a.revision=(SELECT MAX(b.revision) FROM marketing_source_coverage b WHERE b.campaign_id=a.campaign_id AND b.environment=a.environment AND b.source=a.source AND b.starts_at=a.starts_at AND b.ends_at=a.ends_at) ORDER BY a.source,a.starts_at LIMIT 1001`, [q.campaign_id, q.environment, new Date(q.end), new Date(q.start)]);
            if (markers.length > 1000) throw resourceError('report_row_limit', 422, '费用核对区间过多，请缩小查询范围');
            const missing = expected.filter(name => !intervalCovered(Date.parse(q.start), Date.parse(q.end), markers.filter(m => m.source === name) as any));
            const spendComplete = expected.length > 0 && !missing.length && !policyChanged;
            const totals: Totals = { impressions: count('impression'), clicks: count('click'), paid_orders: safe(orders.paid_orders), cohort_orders: safe(cohort.n), refunded_orders: safe(orders.refunded_orders), revenue_minor: safe(orders.revenue_minor), spend_minor: safe(spent.n) };
            const attributionEnd = new Date(Date.parse(q.end) + 7 * DAY).toISOString();
            const observationEnd = new Date(Date.parse(q.end) + 37 * DAY).toISOString();
            const closed = now.getTime() >= Date.parse(observationEnd);
            const warnings = [
                ...(!current ? ['aggregation_lag'] : []), ...(!closed ? ['observation_open'] : []),
                ...(!expected.length ? ['spend_sources_not_declared'] : []), ...(!spendComplete ? ['spend_coverage_incomplete'] : []),
                ...(policyChanged ? ['spend_source_policy_changed'] : []),
                ...(totals.clicks > totals.impressions ? ['clicks_exceed_impressions'] : []),
                ...(totals.cohort_orders > totals.clicks ? ['multiple_orders_per_click'] : []),
            ];
            const revisions = await rows(c, `creative_id,creative_version,day,revision,input_epoch FROM marketing_metric_heads WHERE brand_id=? AND environment=? AND campaign_id=? AND rule_version=?
                AND day>=DATE(DATE_ADD(?,INTERVAL 8 HOUR)) AND day<=DATE(DATE_ADD(?,INTERVAL 8 HOUR))${orderSuffix} ORDER BY day,creative_id,creative_version LIMIT 1001`, [...scope, q.rule_version, new Date(q.start), new Date(Date.parse(q.end) - 1), ...creative]);
            if (revisions.length > 1000) throw resourceError('report_row_limit', 422, '素材版本过多，请按素材查询');
            const queue = await rows(c, "status,COUNT(*) AS n,MIN(created_at) AS oldest FROM marketing_outbox WHERE brand_id=? AND environment=? AND status<>'done' GROUP BY status", [q.brand_id, q.environment]);
            const reportId = randomUUID();
            const payload = {
                report_id: reportId, revision: 1, query: q, currency: 'CNY', data_source: q.environment,
                generated_at: now.toISOString(), received_cutoff: iso(state?.received_cutoff) || now.toISOString(),
                status: !current ? 'stale' : closed && spendComplete ? 'complete' : 'provisional',
                totals: current ? totals : { ...totals, paid_orders: null, cohort_orders: null, refunded_orders: null, revenue_minor: null },
                metrics: calculateMetrics(totals, { current, spendComplete, observationClosed: closed }), warnings,
                observation: { click_window: { start: q.start, end: q.end }, attribution_ends_at: attributionEnd, late_arrival_ends_at: observationEnd, status: closed ? 'closed' : 'open' },
                coverage: { status: spendComplete ? 'complete' : 'incomplete', expected_sources: expected, missing_sources: missing, policy_changed: policyChanged,
                    markers: markers.map(m => ({ ...m, starts_at: iso(m.starts_at), ends_at: iso(m.ends_at) })) },
                evidence: { calculation: 'exact_event_window_and_published_attribution', input_epoch: String(input?.epoch || 0), published_input_epoch: String(state?.input_epoch || 0), aggregation_generation: String(state?.generation || 0),
                    partitions: revisions.map(r => ({ ...r, day: new Date(r.day).toISOString().slice(0, 10), input_epoch: String(r.input_epoch) })),
                    queue: queue.map(r => ({ status: r.status, count: safe(r.n), oldest_at: iso(r.oldest) })),
                    invalid_delivery_events: counts.filter(r => r.status !== 'valid').reduce((n, r) => n + safe(r.n), 0) },
                definitions: { ctr: '有效点击/有效曝光；不截断超过100%的值', cvr: '点击窗口内点击所归因的已支付订单/有效点击；可一点击多订单',
                    cost: 'CPA/CPC/CPM按区间费用计算；费用仅在全部来源核对完整时可用', roas: '支付窗口归因净收入/费用窗口花费，包含退款修订；不是ROI或因果收益',
                    realized_revenue_per_mille_minor: '支付窗口归因净收入/曝光窗口曝光×1000；历史跨期运营指标，不是预估eCPM', monetary_values: '金额为CNY整数分；比值以六位小数字符串返回' },
            };
            const payloadHash = hash(payload), ref = 'mysql:marketing_report_payloads/' + reportId;
            await c.execute('INSERT INTO marketing_report_snapshots(id,brand_id,environment,created_by,`query`,query_hash,revision,received_cutoff,payload_ref,payload_hash) VALUES (?,?,?,?,?,?,1,?,?,?)', [reportId, q.brand_id, q.environment, principal, JSON.stringify(q), hash(q), new Date(payload.received_cutoff), ref, payloadHash]);
            await c.execute('INSERT INTO marketing_report_payloads(report_id,payload) VALUES (?,?)', [reportId, JSON.stringify(payload)]);
            await c.commit();
            return payload;
        } catch (error) { await c.rollback(); throw error; }
    });
}

export async function getReport(principal: string, environment: string, id: string) {
    return withMarketingConnection(async c => {
        const snapshots = await rows(c, 's.brand_id,s.payload_hash,p.payload FROM marketing_report_snapshots s JOIN marketing_report_payloads p ON p.report_id=s.id WHERE s.id=? AND s.environment=?', [id, environment]);
        if (!snapshots.length) throw resourceError('report_not_found', 404, '报表快照不存在');
        await authorize(c, principal, snapshots[0].brand_id, environment);
        const payload = decoded(snapshots[0].payload);
        if (hash(payload) !== snapshots[0].payload_hash) throw resourceError('report_snapshot_corrupt', 503, '报表快照校验失败');
        return payload;
    });
}
