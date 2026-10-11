import { loadMarketing } from '../config/marketing';
import { requireContract } from '../services/marketing/contracts';
import { runSynthetic, SyntheticScope } from '../services/marketing/synthetic';
async function main() {
    const args = process.argv.slice(2), options: Record<string, string> = {};
    if (args.length === 1 && args[0] === '--help') { console.log('seed:marketing-events --base URL --campaign-id ID --creative-id ID --brand-id ID --product-id ID --run-id ID --occurred-at UTC; token: MARKETING_SEED_SOURCE_TOKEN'); return; }
    for (let i = 0; i < args.length; i += 2) {
        const name = args[i]?.replace(/^--/, '');
        if (!['base', 'campaign-id', 'creative-id', 'brand-id', 'product-id', 'run-id', 'occurred-at'].includes(name) || !args[i + 1] || options[name]) throw new Error('invalid_seed_arguments');
        options[name] = args[i + 1];
    }
    for (const key of ['campaign-id', 'creative-id', 'brand-id', 'product-id', 'run-id']) requireContract('Id', options[key]);
    if (options['run-id'].length > 80) throw new Error('seed_run_id_too_long');
    requireContract('UtcTime', options['occurred-at']);
    const token = process.env.MARKETING_SEED_SOURCE_TOKEN;
    if (!token) throw new Error('seed_source_token_required');
    const scope: SyntheticScope = { campaign_id: options['campaign-id'], creative_id: options['creative-id'], creative_version: 1, brand_id: options['brand-id'], product_id: options['product-id'], run_id: options['run-id'], occurred_at: options['occurred-at'] };
    console.log(JSON.stringify(await runSynthetic(options.base, token, loadMarketing().environment, scope)));
}
if (require.main === module) main().catch(e => { console.error(/^synthetic_|^invalid_seed|^seed_|^invalid_request/.test(e.message) ? e.message : 'marketing_seed_failed'); process.exitCode = 1; });
