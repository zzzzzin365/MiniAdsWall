const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const mysql = require('mysql2/promise');

async function database(t, source) {
    const binary = process.env.ADS_TEST_MYSQLD || ['/opt/homebrew/opt/mysql@8.4/bin/mysqld', '/usr/sbin/mysqld'].find(fs.existsSync);
    if (!binary || !fs.existsSync(binary)) { t.skip('real MySQL required; set ADS_TEST_MYSQLD'); return; }
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ads-mysql-'));
    const socket = path.join(root, 'mysql.sock'), log = path.join(root, 'mysql.log');
    const initialized = spawnSync(binary, ['--no-defaults', '--initialize-insecure', '--datadir=' + root + '/db', '--log-error=' + log], { timeout: 60000 });
    if (initialized.status !== 0) { const error = fs.existsSync(log) ? fs.readFileSync(log, 'utf8') : String(initialized.error); fs.rmSync(root, { recursive: true, force: true }); throw new Error(error); }
    const server = spawn(binary, ['--no-defaults', '--datadir=' + root + '/db', '--socket=' + socket, '--skip-networking', '--mysqlx=OFF', '--pid-file=' + root + '/mysql.pid', '--log-error=' + log, '--innodb-buffer-pool-size=64M'], { stdio: 'ignore' });
    let connection, stopped = false;
    const deadline = Date.now() + 60000;
    async function stop() {
        await require('../../dist/services/ads.database').closeAdsDatabase();
        if (stopped) return;
        stopped = true;
        await connection?.end().catch(() => {});
        if (server.exitCode === null) {
            const done = new Promise(resolve => server.once('exit', resolve));
            server.kill('SIGTERM');
            const timer = setTimeout(() => server.kill('SIGKILL'), 10000);
            await done; clearTimeout(timer);
        }
        fs.rmSync(root, { recursive: true, force: true });
    }
    try {
        while (!connection) {
            try { connection = await mysql.createConnection({ socketPath: socket, user: 'root', connectTimeout: 1000, timezone: 'Z' }); }
            catch { if (Date.now() > deadline || server.exitCode !== null) throw new Error(fs.readFileSync(log, 'utf8')); await new Promise(r => setTimeout(r, 100)); }
        }
        await connection.query('CREATE DATABASE ads_verify CHARACTER SET utf8mb4 COLLATE utf8mb4_bin');
        await connection.query('USE ads_verify');
        const env = { ADS_MYSQL_URL: 'mysql://root@localhost/ads_verify', ADS_MYSQL_SOCKET: socket };
        Object.assign(process.env, env);
        const sourcePath = path.join(root, 'legacy.json');
        fs.writeFileSync(sourcePath, JSON.stringify(source || require('../../dist/config').default.DEFAULT_ADS));
        await require('../../dist/scripts/migrate-ads').migrateAds(sourcePath);
        return { env, root, sourcePath, connection, stop };
    } catch (error) { await stop(); throw error; }
}
module.exports = { database };
