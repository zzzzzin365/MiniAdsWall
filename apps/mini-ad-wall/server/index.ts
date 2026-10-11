import app from './app';
import config from './config';
import adsModel from './models/ads.model';
import { marketingMigrationStatus, requireMarketingSchema } from './services/marketing/migrations';

async function main() {
    await adsModel.initialize();
    if (config.MARKETING.features.MARKETING_EVENTS_ENABLED) {
        await requireMarketingSchema(12);
        await marketingMigrationStatus();
    }
    const server = app.listen(Number(config.PORT), config.HOST, () => {
        console.log(`Server running on port ${config.PORT}`);
    });
    let stopping = false;
    const stop = () => {
        if (stopping) return;
        stopping = true;
        const deadline = setTimeout(() => process.exit(1), 10000).unref();
        server.close(async () => {
            await adsModel.close(); clearTimeout(deadline);
        });
    };
    process.once('SIGINT', stop); process.once('SIGTERM', stop);
}
main().catch(async () => {
    console.error('启动失败：请检查 ADS_MYSQL_URL 和广告迁移；开启营销时先执行 npm run migrate:marketing -- up');
    await adsModel.close(); process.exitCode = 1;
});
