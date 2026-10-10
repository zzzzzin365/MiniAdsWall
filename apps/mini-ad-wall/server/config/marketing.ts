export const marketingFlags = [
    'MARKETING_EVENTS_ENABLED', 'MARKETING_CONTENT_ENABLED', 'MARKETING_AGENT_ACTIONS_ENABLED',
    'MARKETING_DELIVERY_ENABLED', 'MARKETING_LEARNING_ENABLED'
] as const;

export function loadMarketing(env: NodeJS.ProcessEnv = process.env) {
    const features: Record<string, boolean> = {};
    for (const [index, key] of marketingFlags.entries()) {
        const raw = env[key] ?? 'false';
        if (!['true', 'false', '1', '0'].includes(raw)) throw new Error('invalid_' + key);
        features[key] = raw === 'true' || raw === '1';
        if (features[key] && index && !features[marketingFlags[index - 1]]) throw new Error('missing_dependency_' + key);
        if (features[key] && index > 0) throw new Error(key + '_not_implemented_yet');
    }
    const environment = env.MARKETING_ENVIRONMENT ?? 'synthetic';
    if (!['synthetic', 'sandbox', 'production'].includes(environment)) throw new Error('invalid_MARKETING_ENVIRONMENT');
    if ((env.MARKETING_PROTOCOL_VERSION ?? '1') !== '1') throw new Error('unsupported_MARKETING_PROTOCOL_VERSION');
    // CP01 only opens resource APIs. Event/serve/write-agent endpoints are not installed.
    return { features, environment: environment as 'synthetic' | 'sandbox' | 'production', protocolVersion: 1 };
}
