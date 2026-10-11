import { RowDataPacket } from 'mysql2/promise';
import { resourceError } from './resource.service';
import { Source, sourceKey, verifyToken } from './security';
import { canonical, enqueue, ingest, serverTime, timeReason, writeEvent } from './ingestion';

const economic = (v: any) => canonical({ type: v.type, order_id: v.order_id, brand_id: v.brand_id, product_id: v.product_id, occurred_at: v.occurred_at, amount_minor: v.amount_minor, currency: v.currency, click_token: v.click_token || null });
export async function conversion(source: Source, input: any) {
    if (!source.brand_ids.includes(input.brand_id)) throw resourceError('source_scope_mismatch', 403, '来源没有该品牌权限');
    // Validate before receipt replay: a rotated key or changed scope cannot bypass authorization.
    const claims = input.click_token ? verifyToken(input.click_token, 'click', source.environment) : null;
    if (claims && (claims.brand_id !== input.brand_id || claims.product_id !== input.product_id)) throw resourceError('click_scope_mismatch', 403, '点击凭证与订单商品不匹配');
    const eventSource = sourceKey(source) + '_conversions';
    return ingest(eventSource, input.source_event_id, input, async c => {
        const [products] = await c.execute<RowDataPacket[]>('SELECT id FROM marketing_products WHERE id=? AND brand_id=? AND environment=?', [input.product_id, input.brand_id, source.environment]);
        if (!products[0]) throw resourceError('product_scope_mismatch', 422, '商品不属于该品牌与环境');
        if (claims) {
            const [links] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_click_links WHERE click_id=? AND environment=?', [claims.click_id, source.environment]);
            if (!links[0] || links[0].click_token !== input.click_token || links[0].visitor_session_id !== claims.visitor_session_id) throw resourceError('click_not_found', 403, '点击凭证没有对应的服务端记录');
        }
        await c.execute('INSERT INTO marketing_order_ingestion(source,order_id,environment,brand_id,product_id) VALUES (?,?,?,?,?) ON DUPLICATE KEY UPDATE order_id=VALUES(order_id)', [eventSource, input.order_id, source.environment, input.brand_id, input.product_id]);
        const [orders] = await c.execute<RowDataPacket[]>('SELECT * FROM marketing_order_ingestion WHERE source=? AND order_id=? AND environment=? FOR UPDATE', [eventSource, input.order_id, source.environment]);
        const order = orders[0];
        if (order.brand_id !== input.brand_id || order.product_id !== input.product_id) throw resourceError('order_scope_conflict', 409, '订单标识已属于其他商品');
        if (input.type === 'order_paid' && order.paid_event_id) {
            const [paid] = await c.execute<RowDataPacket[]>('SELECT payload FROM marketing_events WHERE id=?', [order.paid_event_id]);
            const original = typeof paid[0].payload === 'string' ? JSON.parse(paid[0].payload) : paid[0].payload;
            if (economic(original) !== economic(input)) throw resourceError('order_payment_conflict', 409, '支付订单已存在且内容不同');
            return { status: 201, body: { receipt_id: order.paid_event_id, validation_status: 'valid', reason: null } };
        }
        const reason = timeReason(input.occurred_at, await serverTime(c));
        let status = reason ? 'quarantined' : 'valid';
        if (!reason && input.type === 'order_refunded') {
            if (!order.paid_event_id) status = 'pending';
            else if (BigInt(order.refunded_minor) + BigInt(input.amount_minor) > BigInt(order.paid_amount_minor)) throw resourceError('refund_exceeds_payment', 422, '累计退款超过支付金额');
        }
        const body = await writeEvent(c, { source: eventSource, source_event_id: input.source_event_id, brand_id: input.brand_id, environment: source.environment, type: input.type, occurred_at: input.occurred_at, order_id: input.order_id, product_id: input.product_id, amount_minor: input.amount_minor, click_id: claims?.click_id, payload: { ...input, visitor_session_id: claims?.visitor_session_id || null }, validation_status: status, isolation_reason: reason || (status === 'pending' ? 'payment_not_received' : null) });
        if (status === 'valid' && input.type === 'order_paid') {
            await c.execute('UPDATE marketing_order_ingestion SET paid_event_id=?,paid_amount_minor=? WHERE source=? AND order_id=? AND environment=?', [body.receipt_id, input.amount_minor, eventSource, input.order_id, source.environment]);
            const [pending] = await c.execute<RowDataPacket[]>(`SELECT e.id,e.amount_minor FROM marketing_events e JOIN marketing_event_validation v ON v.event_id=e.id AND v.revision=(SELECT MAX(revision) FROM marketing_event_validation WHERE event_id=e.id) WHERE e.source=? AND e.order_id=? AND e.environment=? AND e.type='order_refunded' AND v.status='pending' ORDER BY e.occurred_at,e.id`, [eventSource, input.order_id, source.environment]);
            let refunded = 0n;
            for (const refund of pending) {
                const valid = refunded + BigInt(refund.amount_minor) <= BigInt(input.amount_minor);
                if (valid) refunded += BigInt(refund.amount_minor);
                await c.execute('INSERT INTO marketing_event_validation(event_id,revision,status,reason) VALUES (?,2,?,?)', [refund.id, valid ? 'valid' : 'quarantined', valid ? null : 'refund_exceeds_payment']);
                await enqueue(c, input.brand_id, source.environment, refund.id, 'marketing.event', { event_id: refund.id, validation_revision: 2 }, 2);
            }
            await c.execute('UPDATE marketing_order_ingestion SET refunded_minor=? WHERE source=? AND order_id=? AND environment=?', [refunded.toString(), eventSource, input.order_id, source.environment]);
        } else if (status === 'valid' && input.type === 'order_refunded') {
            await c.execute('UPDATE marketing_order_ingestion SET refunded_minor=refunded_minor+? WHERE source=? AND order_id=? AND environment=?', [input.amount_minor, eventSource, input.order_id, source.environment]);
        }
        return { status: status === 'valid' ? 201 : 202, body };
    });
}
