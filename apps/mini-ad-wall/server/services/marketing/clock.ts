import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { withAdsConnection } from '../ads.database';

// mysql2 timezone:'Z' only controls serialization. DATETIME defaults still use
// the MySQL session timezone; scope UTC to marketing and restore before release.
export async function withMarketingConnection<T>(work: (c: PoolConnection) => Promise<T>): Promise<T> {
    return withAdsConnection(async c => {
        const [rows] = await c.query<RowDataPacket[]>('SELECT @@session.time_zone AS zone');
        const previous = rows[0].zone;
        await c.query("SET time_zone='+00:00'");
        try { return await work(c); }
        finally { await c.query('SET time_zone=?', [previous]); }
    });
}
