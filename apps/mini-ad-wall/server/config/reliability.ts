export const reliabilityFlags = ['AD_CONTEXT_V1_ENABLED', 'SHARED_ASSET_WRITES_ENABLED', 'NARROW_TRANSACTIONS_ENABLED', 'SSE_SHARED_READER_ENABLED', 'DYNAMIC_EXECUTOR_ENABLED'] as const;
export function loadReliability(env: NodeJS.ProcessEnv = process.env) {
    const deploymentMode = env.BACKEND_DEPLOYMENT_MODE || 'development';
    if (!['development', 'multi_node'].includes(deploymentMode)) throw new Error('invalid_BACKEND_DEPLOYMENT_MODE');
    for (const key of ['HOSTING_INPUT_PROTOCOL_VERSION', 'HOSTING_EXECUTOR_PROTOCOL_VERSION']) {
        if ((env[key] ?? '1') !== '1') throw new Error('unsupported_' + key);
    }
    const features: Record<string, boolean> = {};
    for (const key of reliabilityFlags) {
        const value = (env[key] ?? 'false').toLowerCase();
        if (!['true', 'false', '1', '0'].includes(value)) throw new Error('invalid_' + key);
        features[key] = value === 'true' || value === '1';
        if (features[key] && key !== 'AD_CONTEXT_V1_ENABLED') throw new Error(key + '_not_implemented_yet');
    }
    if (deploymentMode === 'multi_node') throw new Error('multi_node_not_implemented_yet');
    return { deploymentMode: deploymentMode as 'development' | 'multi_node', inputProtocolVersion: 1, executorProtocolVersion: 1, features };
}
