import { createPool, Pool, PoolConnection } from 'mysql2/promise';

let pool: Pool | undefined;

export function adsPool(): Pool {
    if (!pool) {
        const value = process.env.ADS_MYSQL_URL;
        if (!value) throw new Error('请配置 ADS_MYSQL_URL 并先执行广告数据库迁移');
        const url = new URL(value);
        if (url.protocol !== 'mysql:' || !url.pathname.slice(1)) throw new Error('ADS_MYSQL_URL 必须是带数据库名的 mysql:// 地址');
        const database = decodeURIComponent(url.pathname.slice(1));
        if (!/^[a-zA-Z0-9_]+$/.test(database)) throw new Error('广告数据库名只能包含字母、数字和下划线');
        pool = createPool({
            host: url.hostname, port: Number(url.port || 3306),
            user: decodeURIComponent(url.username), password: decodeURIComponent(url.password), database,
            socketPath: process.env.ADS_MYSQL_SOCKET || undefined,
            charset: 'utf8mb4', timezone: 'Z', supportBigNumbers: true, bigNumberStrings: true,
            connectionLimit: 10, maxIdle: 10, idleTimeout: 60000,
            waitForConnections: true, queueLimit: 50, connectTimeout: 3000
        });
    }
    return pool;
}

export async function withAdsConnection<T>(work: (connection: PoolConnection) => Promise<T>): Promise<T> {
    let connection: PoolConnection | undefined;
    try {
        connection = await adsPool().getConnection();
        return await work(connection);
    } catch (error: any) {
        if (error.status) throw error;
        throw Object.assign(new Error('广告存储暂不可用，请稍后重试'), { status: 503 });
    } finally { connection?.release(); }
}

export async function closeAdsDatabase(): Promise<void> {
    const current = pool; pool = undefined;
    if (current) await current.end();
}
