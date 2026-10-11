import { coverageDigest } from './spend.service';
export type SyntheticScope = { campaign_id: string; creative_id: string; creative_version: number; brand_id: string; product_id: string; run_id: string; occurred_at: string };
export function syntheticCases(s: SyntheticScope) {
    const paid = { source_event_id: s.run_id + '_paid', type: 'order_paid', order_id: s.run_id + '_order', brand_id: s.brand_id, product_id: s.product_id, occurred_at: s.occurred_at, amount_minor: 5000, currency: 'CNY' };
    const refund = { ...paid, type: 'order_refunded', source_event_id: s.run_id + '_refund', amount_minor: 1000 };
    const spend = { source_entry_id: s.run_id + '_spend', campaign_id: s.campaign_id, creative_id: s.creative_id, creative_version: s.creative_version, occurred_at: s.occurred_at, amount_minor: 1000, currency: 'CNY' };
    const digest = coverageDigest([spend]);
    const at = new Date(s.occurred_at).getTime();
    const coverage = { source_marker_id: s.run_id + '_marker', campaign_id: s.campaign_id, start: new Date(at).toISOString(), end: new Date(at + 1).toISOString(), ...digest, amount_minor: Number(digest.amount_minor) };
    return [
        { label: 'refund-before-paid', endpoint: 'conversions', input: refund, status: 202 },
        { label: 'payment', endpoint: 'conversions', input: paid, status: 201 },
        { label: 'duplicate-payment', endpoint: 'conversions', input: paid, status: 201 },
        // Receipt is the initial acceptance; latest validation changes separately.
        { label: 'duplicate-refund', endpoint: 'conversions', input: refund, status: 202 },
        { label: 'excess-refund', endpoint: 'conversions', input: { ...refund, source_event_id: s.run_id + '_excess', amount_minor: 5000 }, status: 422 },
        { label: 'spend', endpoint: 'spend-entries', input: spend, status: 201 },
        { label: 'duplicate-spend', endpoint: 'spend-entries', input: spend, status: 201 },
        { label: 'amount-conflict', endpoint: 'spend-entries', input: { ...spend, amount_minor: 1001 }, status: 409 },
        { label: 'source-coverage', endpoint: 'spend-coverage', input: coverage, status: 201 }
    ];
}
export async function runSynthetic(base: string, token: string, environment: string, scope: SyntheticScope) {
    const url = new URL(base);
    if (!['synthetic', 'sandbox'].includes(environment) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.protocol !== 'http:') throw new Error('synthetic_local_environment_required');
    const display = await fetch(new URL('/api/marketing/serve', url), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ campaign_id: scope.campaign_id }) });
    const opportunity: any = await display.json();
    if (!display.ok || opportunity.environment !== environment) throw new Error('synthetic_environment_mismatch');
    const results: any[] = [];
    for (const example of syntheticCases(scope)) {
        const response = await fetch(new URL('/api/marketing/' + example.endpoint, url), { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }, body: JSON.stringify(example.input) });
        const body: any = await response.json();
        if (response.status !== example.status) throw new Error('synthetic_unexpected_' + example.label + '_' + response.status);
        results.push({ label: example.label, status: response.status, validation_status: body.validation_status, coverage_status_at_acceptance: body.coverage_status_at_acceptance, missing_sources: body.missing_sources });
    }
    return results;
}
