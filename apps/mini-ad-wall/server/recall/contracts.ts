export interface Receipt { shard_id: number; seq: string; index_revision: string }
export interface Field { field_id: string; field_key: string; cardinality: 'scalar' | 'multi'; max_values: number }
export interface Condition { field: string; op: 'in' | 'not_in' | 'exists' | 'missing'; values?: string[]; missing?: 'exclude' | 'include' }
export interface Search { conditions: Condition[]; limit: number; min_receipts: Receipt[] }
export interface Document { doc_id: number; index_revision: string; attributes: Record<string, string[]>; enabled: boolean; deleted: boolean; starts_at: string | null; ends_at: string | null; score: string }
export const enabled = () => process.env.ADS_RECALL_ENABLED === 'true';
export const failure = (code: string, status = 400) => Object.assign(new Error(code), { status, code });
export function normalize(value: unknown): string {
    if (typeof value !== 'string' || !value || Buffer.byteLength(value.normalize('NFC')) > 256) throw failure('invalid_attribute');
    return value.normalize('NFC');
}
export function attributes(input: unknown, fields: Field[]): Record<string, string[]> {
    if (!input || typeof input !== 'object' || Array.isArray(input) || Buffer.byteLength(JSON.stringify(input)) > 65536) throw failure('invalid_attribute');
    const entries = Object.entries(input);
    if (entries.length > 128) throw failure('invalid_attribute');
    let total = 0; const output = Object.create(null);
    for (const [key, raw] of entries) {
        const field = fields.find(f => f.field_key === key);
        if (!field || !Array.isArray(raw)) throw failure('invalid_attribute');
        const values = [...new Set(raw.map(normalize))]; total += values.length;
        if (values.length > (field.cardinality === 'scalar' ? 1 : Math.min(50, field.max_values)) || total > 512) throw failure('invalid_attribute');
        if (values.length) output[key] = values;
    }
    return output;
}
export function eligibility(input: any): any {
    if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).some(k => !['enabled', 'starts_at', 'ends_at'].includes(k))) throw failure('invalid_attribute');
    const output: any = {};
    if ('enabled' in input) { if (typeof input.enabled !== 'boolean') throw failure('invalid_attribute'); output.enabled = input.enabled; }
    for (const key of ['starts_at', 'ends_at']) if (key in input) {
        const value = input[key];
        if (value !== null && (typeof value !== 'string' || !/(Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value)))) throw failure('invalid_attribute');
        output[key] = value === null ? null : new Date(value).toISOString();
    }
    if (output.starts_at && output.ends_at && output.starts_at >= output.ends_at) throw failure('invalid_attribute');
    return output;
}
export function search(input: any): Search {
    if (!input || typeof input !== 'object' || Array.isArray(input) || Buffer.byteLength(JSON.stringify(input)) > 32768 || Object.keys(input).some(k => !['conditions', 'limit', 'min_receipts'].includes(k))) throw failure('invalid_filter');
    const conditions = input.conditions ?? [];
    if (!Array.isArray(conditions) || conditions.length > 16) throw failure('invalid_filter');
    let total = 0;
    const parsed = conditions.map((c: any) => {
        if (!c || typeof c !== 'object' || Array.isArray(c) || Object.keys(c).some(k => !['field', 'op', 'values', 'missing'].includes(k)) || !/^[a-z0-9_]{1,64}$/.test(c.field) || !['in', 'not_in', 'exists', 'missing'].includes(c.op)) throw failure('invalid_filter');
        const result: Condition = { field: c.field, op: c.op };
        if (['in', 'not_in'].includes(c.op)) {
            if (!Array.isArray(c.values) || !c.values.length) throw failure('invalid_filter');
            result.values = [...new Set(c.values.map(normalize))] as string[]; total += result.values.length;
            if (result.values.length > 50 || total > 200) throw failure('invalid_filter');
        } else if ('values' in c) throw failure('invalid_filter');
        if (c.op === 'not_in') {
            result.missing = c.missing ?? 'exclude';
            if (!['exclude', 'include'].includes(result.missing)) throw failure('invalid_filter');
        } else if ('missing' in c) throw failure('invalid_filter');
        return result;
    });
    const limit = input.limit ?? 50;
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw failure('invalid_filter');
    const receipts = input.min_receipts ?? [];
    if (!Array.isArray(receipts) || receipts.length > 16 || receipts.some(r => !r || !Number.isInteger(r.shard_id) || r.shard_id < 0 || r.shard_id > 31 || !/^[0-9]{1,20}$/.test(r.seq) || !/^[0-9]{1,20}$/.test(r.index_revision) || BigInt(r.seq) > 18446744073709551615n || BigInt(r.index_revision) > 18446744073709551615n)) throw failure('invalid_filter');
    return { conditions: parsed, limit, min_receipts: receipts };
}
export function matches(doc: Document, conditions: Condition[], time: number): boolean {
    if (!doc.enabled || doc.deleted || (doc.starts_at && Date.parse(doc.starts_at) > time) || (doc.ends_at && Date.parse(doc.ends_at) <= time)) return false;
    return conditions.every(c => {
        const values = doc.attributes[c.field] || [], present = values.length > 0;
        if (c.op === 'exists') return present;
        if (c.op === 'missing') return !present;
        const hit = (c.values || []).some(v => values.includes(v));
        return c.op === 'in' ? hit : !hit && (present || c.missing === 'include');
    });
}
