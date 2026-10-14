import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';

// Read the same immutable ledger as the dashboard, in source and compiled layouts.
// A deployed server without this demo asset reports missing metrics, never zeroes.
function ledger() {
    const file = [resolve(__dirname, '../../../client/src/data/merchant-demo.json'),
        resolve(__dirname, '../../client/src/data/merchant-demo.json')].find(existsSync);
    if (!file) return null;
    const value = JSON.parse(readFileSync(file, 'utf8'));
    if (value.schema_version !== 1 || value.environment !== 'synthetic') throw new Error('invalid_demo_ledger');
    return value;
}
const fixture = ledger();
const fields = ['impressions', 'clicks', 'orders', 'spend_minor', 'gross_revenue_minor', 'refund_minor', 'net_revenue_minor'] as const;
const empty = () => Object.fromEntries(fields.map(key => [key, 0]));
function rates(row: Record<string, number>) {
    return { ctr: row.impressions ? row.clicks / row.impressions : null,
        click_to_order_rate: row.clicks ? row.orders / row.clicks : null,
        average_cpc_minor: row.clicks ? row.spend_minor / row.clicks : null,
        cost_per_paid_order_minor: row.orders ? row.spend_minor / row.orders : null,
        net_roas: row.spend_minor ? row.net_revenue_minor / row.spend_minor : null };
}

export function assistantPerformance(context: any, range: unknown = '7') {
    if (range !== '7' && range !== '3') {
        const error: any = new Error('invalid_performance_range'); error.status = 400; throw error;
    }
    const days: string[] = fixture ? (range === '7' ? fixture.days : fixture.days.slice(-3)) : [];
    const summary = empty(), items: any[] = [], missing: string[] = [], unlaunched: any[] = [];
    for (const current of context.items) {
        const sample = fixture?.ads.find((ad: any) => ad.id === current.id);
        if (!sample) {
            missing.push(current.id);
            unlaunched.push({ad_id:current.id,title:current.title,delivery_status:fixture ? 'not_delivered' : 'unknown'});
            continue;
        }
        const row = empty();
        for (const day of sample.daily.filter((day: any) => days.includes(day.day))) {
            row.impressions += day.impressions;
            row.clicks += day.click_costs_minor.length;
            row.spend_minor += day.click_costs_minor.reduce((a: number, b: number) => a + b, 0);
        }
        const paid = fixture.orders.filter((order: any) => order.ad_id === current.id && days.includes(order.paid_at));
        const refunded = fixture.refunds.filter((refund: any) => days.includes(refund.refunded_at) &&
            fixture.orders.some((order: any) => order.id === refund.order_id && order.ad_id === current.id));
        row.orders = paid.length;
        row.gross_revenue_minor = paid.reduce((sum: number, order: any) => sum + order.amount_minor, 0);
        row.refund_minor = refunded.reduce((sum: number, refund: any) => sum + refund.amount_minor, 0);
        row.net_revenue_minor = row.gross_revenue_minor - row.refund_minor;
        for (const key of fields) summary[key] += row[key];
        items.push({ ad_id: current.id, title: current.title, tested_material_id: sample.material_id,
            delivery_status: sample.daily.some((day: any) => day.impressions > 0) ? 'delivered' : 'not_delivered',
            paused: sample.status.startsWith('暂停'), daily_budget_minor: sample.daily_budget_minor,
            max_cpc_minor: Number.isFinite(current.price) ? Math.round(current.price * 100) : null,
            unit_price_minor: sample.unit_price_minor, unit_cost_minor: sample.unit_cost_minor,
            tested_material_name: sample.material_name, current_material_id: current.material_id ?? null,
            material_changed: current.material_id !== sample.material_id,
            ...row, ...rates(row) });
    }
    return { version: 1, source: fixture ? 'ad_performance_ledger' : 'metrics_unavailable',
        label: fixture ? '广告投放数据' : '暂无投放指标',
        captured_at: context.captured_at, range, window: days, currency: 'CNY', money_unit: 'fen',
        scope: 'selected_authorized_ads', selection: context.selection, selected_count: context.items.length,
        measured_count: items.length, missing_ad_ids: missing, unlaunched_ads: unlaunched,
        account_daily_budget_minor: fixture?.merchant_daily_budget_minor ?? null,
        definitions: { orders: '窗口内支付订单数', net_revenue: '窗口内支付金额减窗口内退款金额；不是利润',
            ctr: '点击数/曝光数', click_to_order_rate: '支付订单数/点击数，非落地页到达转化率',
            net_roas: '净收入/广告花费，非投资利润率', database_clicks: '广告库累计点击，不与窗口内账本点击混算' },
        missing_information: ['当前预算余额', '物流、手续费等完整履约成本'],
        summary: { ...summary, ...rates(summary) }, items };
}
