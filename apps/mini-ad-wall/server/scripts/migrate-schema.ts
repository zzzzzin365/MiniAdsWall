import { adsMigrationStatus, migrateAdsSchema, AdsMigrationError } from '../services/ads.migrations';
import { closeAdsDatabase } from '../services/ads.database';

async function main() {
    const args = process.argv.slice(2);
    if (args[0] === '--help') { console.log('npm run migrate:schema -- [up|status] [--target N] [--lock-timeout 0..60]'); return; }
    const command = args.shift() || 'up';
    if (!['up', 'status'].includes(command)) throw new AdsMigrationError('invalid_command');
    const options: { target?: number; lockTimeout?: number } = {};
    while (args.length) {
        const flag = args.shift(), value = args.shift();
        if (!value || !/^\d+$/.test(value)) throw new AdsMigrationError('invalid_option');
        if (flag === '--target' && command === 'up') options.target = Number(value);
        else if (flag === '--lock-timeout' && command === 'up') options.lockTimeout = Number(value);
        else throw new AdsMigrationError('invalid_option');
    }
    console.log(JSON.stringify(command === 'status' ? await adsMigrationStatus() : await migrateAdsSchema(options)));
}
main().catch(error => { console.error(error instanceof AdsMigrationError ? error.message : 'schema_migration_failed'); process.exitCode = 1; }).finally(closeAdsDatabase);
