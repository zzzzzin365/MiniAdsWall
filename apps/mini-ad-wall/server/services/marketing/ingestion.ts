import { createHash, randomUUID } from 'crypto';
import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { withMarketingConnection } from './clock';
import { resourceError } from './resource.service';
import { protocol } from './security';

export function canonical(value: any): string {
    if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
    if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
    return JSON.stringify(value);
}
export const hash = (value: any) => createHash('sha256').update(canonical(value)).digest('hex');
export type Receipt = { status: number; body: any };
export async function ingest(source: string, id: string, payload: any, work: (c: PoolConnection) => Promise<Receipt>): Promise<Receipt> {
    return withMarketingConnection(async c => {
        await c.query('SET TRANSACTION ISOLATION LEVEL READ COMMITTED');
        await c.beginTransaction();
        try {
            const fingerprint = hash(payload);
            try {
                await c.execute('INSERT INTO marketing_ingestion_receipts(source,source_event_id,payload_hash) VALUES (?,?,?)', [source, id, fingerprint]);
            } catch (e) {
                if ((e as any).code !== 'ER_DUP_ENTRY') throw e;
                const [rows] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_ingestion_receipts WHERE source=? AND source_event_id=?', [source, id]);
                if (!rows[0] || rows[0].payload_hash !== fingerprint) throw resourceError('event_id_conflict', 409, '事件标识已用于不同内容');
                if (rows[0].response_status === null) throw resourceError('event_pending', 409, '事件处理尚未完成');
                await c.rollback();
                return { status: rows[0].response_status, body: typeof rows[0].response_body === 'string' ? JSON.parse(rows[0].response_body) : rows[0].response_body };
            }
            const result = await work(c);
            await c.execute('UPDATE marketing_ingestion_receipts SET response_status=?,response_body=? WHERE source=? AND source_event_id=?', [result.status, JSON.stringify(result.body), source, id]);
            await c.commit();
            return result;
        } catch (e) { await c.rollback(); throw e; }
    });
}
export async function serverTime(c: PoolConnection): Promise<Date> {
    const [r] = await c.query<RowDataPacket[]>('SELECT UTC_TIMESTAMP(3) AS now');
    return r[0].now;
}
export function timeReason(occurredAt: string, now: Date) {
    const delta = new Date(occurredAt).getTime() - now.getTime();
    return delta > protocol.futureMinutes * 60000 ? 'event_from_future' : delta < -protocol.lateDays * 86400000 ? 'event_too_old' : null;
}
export async function enqueue(c: PoolConnection, brand: string, environment: string, aggregate: string, kind: string, payload: any, revision = 1) {
    // Serialize the input clock with every accepted change in the SAME transaction.
    // An outbox auto-increment ID alone does not describe commit order.
    await c.execute('INSERT INTO marketing_input_epochs(brand_id,environment,epoch) VALUES (?,?,1) ON DUPLICATE KEY UPDATE epoch=epoch+1', [brand, environment]);
    const [queue] = await c.execute<RowDataPacket[]>("SELECT COUNT(*) AS n FROM (SELECT id FROM marketing_outbox WHERE brand_id=? AND environment=? AND status<>'done' LIMIT 10000) bounded", [brand, environment]);
    if (Number(queue[0].n) >= 10000) throw resourceError('marketing_backlog_full', 503, '营销任务积压已达上限');
    await c.execute('INSERT INTO marketing_outbox(kind,aggregate_id,revision,brand_id,environment,payload) VALUES (?,?,?,?,?,?)', [kind, aggregate, revision, brand, environment, JSON.stringify(payload)]);
}
export async function writeEvent(c: PoolConnection, input: { source: string; source_event_id: string; brand_id: string; environment: string; type: string; occurred_at: string; payload: any; impression_id?: string; click_id?: string; order_id?: string; product_id?: string; amount_minor?: number; validation_status?: string; isolation_reason?: string | null }) {
    const id = randomUUID(), status = input.validation_status || 'valid';
    await c.execute(`INSERT INTO marketing_events(id,brand_id,environment,source,source_event_id,type,occurred_at,impression_id,click_id,order_id,product_id,amount_minor,payload_hash,payload,validation_status,isolation_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, [id, input.brand_id, input.environment, input.source, input.source_event_id, input.type, new Date(input.occurred_at), input.impression_id || null, input.click_id || null, input.order_id || null, input.product_id || null, input.amount_minor ?? null, hash(input.payload), JSON.stringify(input.payload), status, input.isolation_reason || null]);
    await c.execute('INSERT INTO marketing_event_validation(event_id,revision,status,reason) VALUES (?,1,?,?)', [id, status, input.isolation_reason || null]);
    await enqueue(c, input.brand_id, input.environment, id, 'marketing.event', { event_id: id, validation_revision: 1 });
    return { receipt_id: id, validation_status: status, reason: input.isolation_reason || null };
}
