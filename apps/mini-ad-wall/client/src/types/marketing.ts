export type MetricValue = { value: string | null; unit: 'ratio' | 'minor'; status: 'complete' | 'provisional' | 'unavailable' | 'estimate'; reasons?: string[]; formula?: string };
export type MarketingReport = {
    report_id: string; revision: number; currency: string; data_source: string;
    query: { brand_id: string; campaign_id: string; environment: string; start: string; end: string; timezone: string; creative_id?: string; creative_version?: number; rule_version: string };
    status: 'complete' | 'provisional' | 'stale'; generated_at: string; received_cutoff: string;
    totals: Record<string, number | null>; metrics: Record<string, MetricValue>; warnings: string[];
    coverage: { status: string; expected_sources: string[]; missing_sources: string[]; policy_changed: boolean };
    observation: { status: string; attribution_ends_at: string; late_arrival_ends_at: string };
};
export type MetricEstimateInput = {
    ecpm?: { predicted_ctr: number; predicted_cvr?: number; bid_minor: number; bid_basis: 'CPA' | 'CPC' };
    ltv?: { average_order_net_revenue_minor: number; orders_per_month: number; lifetime_months: number };
};
export type MetricEstimates = { currency: string; input_source: string; metrics: Record<string, MetricValue> };
