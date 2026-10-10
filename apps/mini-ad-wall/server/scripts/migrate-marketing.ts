import { marketingMigrationStatus, migrateMarketingSchema } from '../services/marketing/migrations';
import { closeAdsDatabase } from '../services/ads.database';

async function main() {
    const args = process.argv.slice(2);
    if (args.length > 1 || !['up', 'status', '--help'].includes(args[0] || 'up')) throw new Error('invalid_command');
    if (args[0] === '--help') { console.log('npm run migrate:marketing -- [up|status]'); return; }
    console.log(JSON.stringify(args[0] === 'status' ? await marketingMigrationStatus() : await migrateMarketingSchema()));
}
if (require.main === module) main().catch(e => { console.error(/^marketing_|invalid_command$/.test(e.message) ? e.message : 'marketing_migration_failed'); process.exitCode = 1; }).finally(closeAdsDatabase);
