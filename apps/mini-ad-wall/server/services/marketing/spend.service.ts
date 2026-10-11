import { createHash, randomUUID } from 'crypto';
import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { resourceError } from './resource.service';
import { Source, sourceKey, sourcePolicies } from './security';
import { canonical, enqueue, hash, ingest, serverTime, timeReason } from './ingestion';
import { withAdsConnection } from '../ads.database';

async function authorizeCurrentScope(source: Source, id: string) {
    await withAdsConnection(async c => {
        const [rows] = await c.execute<RowDataPacket[]>('SELECT brand_id FROM marketing_campaigns WHERE id=? AND environment=?', [id, source.environment]);
        if (!rows[0] || !source.brand_ids.includes(rows[0].brand_id)) throw resourceError('source_scope_mismatch', 403, '来源没有该活动权限');
    });
}

// Frozen v1: binary source-entry order, canonical JSON + LF per row; empty = SHA256('').
export function coverageDigest(rows: any[]) {
    const sorted = [...rows].sort((a, b) => Buffer.compare(Buffer.from(a.source_entry_id), Buffer.from(b.source_entry_id)));
    const content = sorted.map(r => canonical({ source_entry_id: r.source_entry_id, creative_id: r.creative_id, creative_version: Number(r.creative_version), occurred_at: new Date(r.occurred_at).toISOString(), amount_minor: Number(r.amount_minor), currency: r.currency }) + '\n').join('');
    const total = rows.reduce((n, r) => n + BigInt(r.amount_minor), 0n);
    return { entry_count: rows.length, amount_minor: total.toString(), checksum: createHash('sha256').update(content).digest('hex') };
}
async function campaign(c: PoolConnection, source: Source, id: string) {
    const [rows] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_campaigns WHERE id=? AND environment=? FOR UPDATE', [id, source.environment]);
    if (!rows[0] || !source.brand_ids.includes(rows[0].brand_id)) throw resourceError('source_scope_mismatch', 403, '来源没有该活动权限');
    const expected = sourcePolicies().filter(s => s.environment === source.environment && s.brand_ids.includes(rows[0].brand_id) && s.interfaces.includes('spend-entries')).map(s => s.name).sort();
    if (!expected.includes(source.name)) throw resourceError('source_not_declared', 403, '费用来源未在活动来源配置中声明');
    const [existing] = await c.execute<RowDataPacket[]>('SELECT source FROM marketing_source_policies WHERE campaign_id=? AND environment=? ORDER BY source', [id, source.environment]);
    if (existing.length && canonical(existing.map(r => r.source)) !== canonical(expected)) throw resourceError('source_policy_changed', 409, '活动费用来源已冻结，修改需新版本');
    if (!existing.length) for (const name of expected) await c.execute('INSERT INTO marketing_source_policies(campaign_id,source,environment) VALUES (?,?,?)', [id, name, source.environment]);
    return { brand_id: rows[0].brand_id as string, expected };
}
export async function spendEntry(source: Source, input: any) {
    await authorizeCurrentScope(source, input.campaign_id);
    return ingest(sourceKey(source) + '_spend', input.source_entry_id, input, async c => {
        const scope = await campaign(c, source, input.campaign_id);
        const [creative] = await c.execute<RowDataPacket[]>('SELECT id FROM marketing_creatives WHERE id=? AND version=? AND campaign_id=? AND brand_id=? AND environment=?', [input.creative_id, input.creative_version, input.campaign_id, scope.brand_id, source.environment]);
        if (!creative[0]) throw resourceError('creative_scope_mismatch', 422, '素材版本不属于该活动');
        if (input.adjustment_ref) {
            const [base] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_spend_ledger WHERE id=? AND source=? AND campaign_id=? AND creative_id=? AND creative_version=? AND adjustment_ref IS NULL FOR UPDATE', [input.adjustment_ref, sourceKey(source), input.campaign_id, input.creative_id, input.creative_version]);
            if (!base[0]) throw resourceError('adjustment_not_found', 422, '更正必须引用同来源同素材的原流水');
            if (base[0].occurred_at.getTime() !== new Date(input.occurred_at).getTime()) throw resourceError('adjustment_time_mismatch', 422, '更正必须使用原流水发生时间');
            const [sum] = await c.execute<RowDataPacket[]>('SELECT COALESCE(SUM(amount_minor),0) AS total FROM marketing_spend_ledger WHERE adjustment_ref=?', [input.adjustment_ref]);
            if (BigInt(base[0].amount_minor) + BigInt(sum[0].total) + BigInt(input.amount_minor) < 0n) throw resourceError('adjustment_exceeds_spend', 422, '更正不能使原流水净费用为负');
        } else if (input.amount_minor < 0) throw resourceError('negative_base_spend', 422, '负费用必须引用原流水');
        const reason = timeReason(input.occurred_at, await serverTime(c)), id = randomUUID();
        if (reason) {
            await c.execute('INSERT INTO marketing_spend_isolation(id,source,source_entry_id,payload,reason) VALUES (?,?,?,?,?)', [id, sourceKey(source), input.source_entry_id, JSON.stringify(input), reason]);
            await c.execute("UPDATE marketing_source_coverage SET status='invalidated',reason='quarantined_spend_present' WHERE campaign_id=? AND environment=? AND source=? AND starts_at<=? AND ends_at>? AND status='complete'", [input.campaign_id, source.environment, source.name, new Date(input.occurred_at), new Date(input.occurred_at)]);
            await enqueue(c, scope.brand_id, source.environment, id, 'marketing.spend-isolated', { reason });
            return { status: 202, body: { receipt_id: id, validation_status: 'quarantined', reason } };
        }
        await c.execute(`INSERT INTO marketing_spend_ledger(id,brand_id,environment,source,source_entry_id,campaign_id,creative_id,creative_version,occurred_at,amount_minor,adjustment_ref,payload_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, [id, scope.brand_id, source.environment, sourceKey(source), input.source_entry_id, input.campaign_id, input.creative_id, input.creative_version, new Date(input.occurred_at), input.amount_minor, input.adjustment_ref || null, hash(input)]);
        await c.execute("UPDATE marketing_source_coverage SET status='invalidated',reason='ledger_changed' WHERE campaign_id=? AND environment=? AND source=? AND starts_at<=? AND ends_at>? AND status='complete'", [input.campaign_id, source.environment, source.name, new Date(input.occurred_at), new Date(input.occurred_at)]);
        await enqueue(c, scope.brand_id, source.environment, id, 'marketing.spend', { entry_id: id });
        return { status: 201, body: { receipt_id: id, validation_status: 'valid', reason: null } };
    });
}
export async function spendCoverage(source: Source, input: any) {
    await authorizeCurrentScope(source, input.campaign_id);
    if (new Date(input.start) >= new Date(input.end)) throw resourceError('invalid_interval', 400, '区间必须满足开始早于结束');
    return ingest(sourceKey(source) + '_coverage', input.source_marker_id, input, async c => {
        const scope = await campaign(c, source, input.campaign_id);
        const params = [input.campaign_id, source.environment, source.name, new Date(input.start), new Date(input.end)];
        const [entries] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_spend_ledger WHERE campaign_id=? AND environment=? AND source=? AND occurred_at>=? AND occurred_at<?', [input.campaign_id, source.environment, sourceKey(source), new Date(input.start), new Date(input.end)]);
        const digest = coverageDigest(entries);
        const [isolated] = await c.execute<RowDataPacket[]>(`SELECT id FROM marketing_spend_isolation WHERE source=? AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.campaign_id'))=? AND CAST(REPLACE(REPLACE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.occurred_at')),'T',' '),'Z','') AS DATETIME(3))>=? AND CAST(REPLACE(REPLACE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.occurred_at')),'T',' '),'Z','') AS DATETIME(3))<? LIMIT 1`, [sourceKey(source), input.campaign_id, new Date(input.start), new Date(input.end)]);
        const matches = !isolated.length && digest.entry_count === input.entry_count && digest.amount_minor === String(input.amount_minor) && digest.checksum === input.checksum;
        const reason = matches ? null : isolated.length ? 'quarantined_spend_present' : 'reconciliation_mismatch';
        const [revisions] = await c.execute<RowDataPacket[]>('SELECT COALESCE(MAX(revision),0) AS revision FROM marketing_source_coverage WHERE campaign_id=? AND environment=? AND source=? AND starts_at=? AND ends_at=?', params);
        const revision = Number(revisions[0].revision) + 1;
        // A failed reconciliation invalidates a prior completion instead of rolling it back.
        await c.execute("UPDATE marketing_source_coverage SET status='invalidated',reason='marker_superseded' WHERE campaign_id=? AND environment=? AND source=? AND starts_at=? AND ends_at=? AND status='complete'", params);
        await c.execute('INSERT INTO marketing_source_coverage(campaign_id,environment,source,starts_at,ends_at,revision,brand_id,expected_count,expected_amount_minor,checksum,status,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [...params, revision, scope.brand_id, input.entry_count, input.amount_minor, input.checksum, matches ? 'complete' : 'incomplete', reason]);
        const [complete] = await c.execute<RowDataPacket[]>(`SELECT a.source FROM marketing_source_coverage a WHERE campaign_id=? AND environment=? AND starts_at=? AND ends_at=? AND status='complete' AND revision=(SELECT MAX(b.revision) FROM marketing_source_coverage b WHERE b.campaign_id=a.campaign_id AND b.environment=a.environment AND b.source=a.source AND b.starts_at=a.starts_at AND b.ends_at=a.ends_at)`, [input.campaign_id, source.environment, new Date(input.start), new Date(input.end)]);
        const missing = scope.expected.filter((name: string) => !complete.some(r => r.source === name));
        const receiptId = hash({ source: source.name, input, revision });
        await enqueue(c, scope.brand_id, source.environment, receiptId, 'marketing.coverage', { campaign_id: input.campaign_id, source: source.name, revision, starts_at: input.start, ends_at: input.end });
        return { status: matches ? 201 : 422, body: { receipt_id: receiptId, revision, source_status: matches ? 'complete' : 'incomplete', receipt_semantics: 'acceptance_snapshot', coverage_status_at_acceptance: missing.length ? 'incomplete' : 'complete', missing_sources: missing, reason } };
    });
}
