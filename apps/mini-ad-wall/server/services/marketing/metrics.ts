import { resourceError } from './resource.service';

export type Totals = {
    impressions: number; clicks: number; paid_orders: number; cohort_orders: number;
    refunded_orders: number; revenue_minor: number; spend_minor: number;
};
export type Metric = { value: string | null; unit: 'ratio' | 'minor'; status: 'provisional' | 'complete' | 'unavailable'; reasons: string[] };

// Keep money and multiplication exact; serialize fractional values as decimal strings.
export function decimalRatio(numerator: number, denominator: number, multiplier = 1): string | null {
    for (const n of [numerator, denominator, multiplier]) if (!Number.isSafeInteger(n) || n < 0) throw resourceError('metric_out_of_range', 422, '指标超出精确计算范围');
    if (!denominator) return null;
    const scale = 1000000n, d = BigInt(denominator);
    const scaled = (BigInt(numerator) * BigInt(multiplier) * scale + d / 2n) / d;
    return `${scaled / scale}.${(scaled % scale).toString().padStart(6, '0')}`;
}

export function calculateMetrics(totals: Totals, conditions: { current: boolean; spendComplete: boolean; observationClosed: boolean }) {
    const { current, spendComplete, observationClosed } = conditions;
    const status = observationClosed ? 'complete' : 'provisional';
    const ratio = (n: number, d: number, unit: Metric['unit'] = 'ratio', cost = false, multiplier = 1): Metric => {
        const reasons = [...(!current ? ['aggregation_lag'] : []), ...(cost && !spendComplete ? ['spend_coverage_incomplete'] : []), ...(!d ? ['zero_denominator'] : [])];
        return { value: reasons.length ? null : decimalRatio(n, d, multiplier), unit, status: reasons.length ? 'unavailable' : status, reasons };
    };
    return {
        ctr: ratio(totals.clicks, totals.impressions),
        cvr: ratio(totals.cohort_orders, totals.clicks),
        cpa_minor: ratio(totals.spend_minor, totals.paid_orders, 'minor', true),
        cpc_minor: ratio(totals.spend_minor, totals.clicks, 'minor', true),
        cpm_minor: ratio(totals.spend_minor, totals.impressions, 'minor', true, 1000),
        roas: ratio(totals.revenue_minor, totals.spend_minor, 'ratio', true),
        refund_rate: ratio(totals.refunded_orders, totals.paid_orders),
        // Observed attributed net revenue, explicitly distinct from predicted eCPM.
        realized_revenue_per_mille_minor: ratio(totals.revenue_minor, totals.impressions, 'minor', false, 1000),
        ecpm_minor: { value: null, unit: 'minor', status: 'unavailable', reasons: ['prediction_model_and_bid_basis_missing'] } as Metric,
        ltv_minor: { value: null, unit: 'minor', status: 'unavailable', reasons: ['stable_customer_identity_and_lifetime_history_missing'] } as Metric,
    };
}

// Forecast calculator only. The caller supplies assumptions; these values never
// enter observed reports, ranking, billing or customer lifetime records.
export function estimateMetrics(input: {
    ecpm?: { predicted_ctr: number; predicted_cvr?: number; bid_minor: number; bid_basis: 'CPC' | 'CPA' };
    ltv?: { average_order_net_revenue_minor: number; orders_per_month: number; lifetime_months: number };
}) {
    const format = (scaled: bigint) => `${scaled / 1000000n}.${(scaled % 1000000n).toString().padStart(6, '0')}`;
    const product = (values: number[]) => {
        let numerator = 1n, denominator = 1n;
        for (const value of values) {
            const [base, exponent = '0'] = String(value).split('e');
            const places = (base.split('.')[1]?.length || 0) - Number(exponent);
            numerator *= BigInt(base.replace('.', ''));
            if (places > 0) denominator *= 10n ** BigInt(places);
            else numerator *= 10n ** BigInt(-places);
        }
        return format((numerator * 1000000n + denominator / 2n) / denominator);
    };
    const result: Record<string, unknown> = {};
    if (input.ecpm) {
        const p = input.ecpm;
        result.ecpm_minor = { value: product([p.bid_minor, 1000, p.predicted_ctr, p.bid_basis === 'CPA' ? p.predicted_cvr! : 1]), unit: 'minor', status: 'estimate',
            formula: p.bid_basis === 'CPA' ? 'pCTR × pCVR × CPA出价 × 1000' : 'pCTR × CPC出价 × 1000' };
    }
    if (input.ltv) {
        const p = input.ltv;
        result.ltv_minor = { value: product([p.average_order_net_revenue_minor, p.orders_per_month, p.lifetime_months]), unit: 'minor', status: 'estimate',
            formula: '平均订单净收入 × 月购买次数 × 预计客户存续月数；收入口径，不是利润' };
    }
    return { currency: 'CNY', input_source: 'user_supplied_assumptions', assumptions: input, metrics: result };
}
