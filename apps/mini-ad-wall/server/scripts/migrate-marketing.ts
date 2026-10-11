import { marketingMigrationStatus, migrateMarketingSchema } from '../services/marketing/migrations';
import { closeAdsDatabase } from '../services/ads.database';

async function main() {
    const args = process.argv.slice(2);
    if (args[0] === '--help' && args.length === 1) { console.log('npm run migrate:marketing -- [up|status] [--target 10|11|12]'); return; }
    if (![0, 1, 3].includes(args.length) || !['up', 'status'].includes(args[0] || 'up') || (args.length === 3 && (args[1] !== '--target' || !['10', '11', '12'].includes(args[2])))) throw new Error('invalid_command');
    const options = args.length === 3 ? { target: Number(args[2]) } : {};
    console.log(JSON.stringify(args[0] === 'status' ? await marketingMigrationStatus(options) : await migrateMarketingSchema(options)));
}
if (require.main === module) main().catch(e => { console.error(/^marketing_|invalid_command$/.test(e.message) ? e.message : 'marketing_migration_failed'); process.exitCode = 1; }).finally(closeAdsDatabase);
