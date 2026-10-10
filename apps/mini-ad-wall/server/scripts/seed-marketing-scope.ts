import { seedMarketingScope } from '../services/marketing/seed';
import { closeAdsDatabase } from '../services/ads.database';

async function main() {
    const argv = process.argv.slice(2), values: Record<string, string> = {};
    if (argv[0] === '--help') { console.log('seed:marketing -- --brand BRAND --product PRODUCT --principal SUBJECT --name NAME --environment synthetic|sandbox --ad-ids ID1,ID2'); return; }
    while (argv.length) {
        const key = argv.shift(), value = argv.shift();
        if (!['--brand', '--product', '--principal', '--name', '--environment', '--ad-ids'].includes(key!) || !value || values[key!]) throw new Error('invalid_seed_arguments');
        values[key!] = value;
    }
    if (Object.keys(values).length !== 6 || !['synthetic', 'sandbox'].includes(values['--environment'])) throw new Error('invalid_seed_arguments');
    console.log(JSON.stringify(await seedMarketingScope({ brand_id: values['--brand'], product_id: values['--product'], principal: values['--principal'], name: values['--name'], environment: values['--environment'] as 'synthetic' | 'sandbox', ad_ids: values['--ad-ids'].split(',') })));
}
if (require.main === module) main().catch(e => { console.error(e.code || 'marketing_seed_failed'); process.exitCode = 1; }).finally(closeAdsDatabase);
