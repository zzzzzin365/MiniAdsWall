import { AsyncLocalStorage } from 'async_hooks';
import { randomUUID } from 'crypto';
import { PoolConnection, ResultSetHeader, RowDataPacket } from 'mysql2/promise';
import * as recall from '../services/recall/repository';
import { enabled } from '../recall/contracts';
import { Receipt } from '../recall/contracts';
import { Ad, AdInput } from '../types';
import { storedPrice } from '../services/ad-value';
import { withAdsConnection, closeAdsDatabase } from '../services/ads.database';

interface Operation { fingerprint: string; status: number; body: any; createdAt: string; recall_receipt?: Receipt }
interface Change { before: Ad | null; after: Ad | null }
interface Transaction { connection: PoolConnection; changes: Change[]; receipt?: Receipt }
const transactions = new AsyncLocalStorage<Transaction>();
const conflict = (message: string) => Object.assign(new Error(message), { status: 409 });
const json = (value: any) => typeof value === 'string' ? JSON.parse(value) : value;
function ad(row: RowDataPacket): Ad {
    return { id: row.id, title: row.title, publisher: row.publisher, content: row.content,
        url: row.url, price: Number(row.price), clicks: Number(row.clicks), videos: json(row.videos), version: row.version };
}
function operation(row: RowDataPacket): Operation {
    return { fingerprint: row.fingerprint, status: row.status, body: json(row.body), createdAt: row.created_at.toISOString(), ...(row.recall_receipt ? {recall_receipt: json(row.recall_receipt)} : {}) };
}
function transaction(): Transaction {
    const current = transactions.getStore();
    if (!current) throw new Error('广告变更必须在幂等业务事务中执行');
    return current;
}

// Marketing resource mutations share the existing idempotency/audit transaction.
export function currentAdsConnection(): PoolConnection { return transaction().connection; }
export function recordOperationChange(before: any, after: any): void { transaction().changes.push({ before, after }); }

async function executeOperation(owner: string, key: string, fingerprint: string, action: string,
    apply: () => Promise<{ status: number; body: any }>): Promise<Operation> {
    return withAdsConnection(async connection => {
        await connection.query('SET TRANSACTION ISOLATION LEVEL READ COMMITTED');
        await connection.beginTransaction();
        try {
            try {
                // The unique key serializes only duplicates of this operation, across all replicas.
                await connection.execute('INSERT INTO ads_business_operations (owner, operation_key, fingerprint) VALUES (?, ?, ?)', [owner, key, fingerprint]);
            } catch (error: any) {
                if (error.code !== 'ER_DUP_ENTRY') throw error;
                const [rows] = await connection.execute<RowDataPacket[]>('SELECT * FROM ads_business_operations WHERE owner = ? AND operation_key = ?', [owner, key]);
                const previous = rows[0];
                if (!previous || previous.fingerprint !== fingerprint) throw conflict('操作标识已用于不同请求');
                if (previous.status === null) throw conflict('操作尚未完成');
                await connection.rollback();
                return operation(previous);
            }
            const current: Transaction = { connection, changes: [] };
            const result = await transactions.run(current, apply);
            await connection.execute('UPDATE ads_business_operations SET status = ?, body = ? WHERE owner = ? AND operation_key = ?',
                [result.status, JSON.stringify(result.body), owner, key]);
            if (current.receipt) await connection.execute('UPDATE ads_business_operations SET recall_receipt=? WHERE owner=? AND operation_key=?', [current.receipt ? JSON.stringify(current.receipt) : null, owner, key]);
            if (!current.changes.length) current.changes.push({ before: null, after: null });
            for (const change of current.changes) {
                await connection.execute('INSERT INTO ads_business_audit (owner, operation_key, action, before_data, after_data) VALUES (?, ?, ?, ?, ?)',
                    [owner, key, action, JSON.stringify(change.before), JSON.stringify(change.after)]);
            }
            const [rows] = await connection.execute<RowDataPacket[]>('SELECT * FROM ads_business_operations WHERE owner = ? AND operation_key = ?', [owner, key]);
            await connection.commit();
            return operation(rows[0]);
        } catch (error) { await connection.rollback(); throw error; }
    });
}

async function getOperation(owner: string, key: string): Promise<Operation | undefined> {
    return withAdsConnection(async connection => {
        const [rows] = await connection.execute<RowDataPacket[]>('SELECT * FROM ads_business_operations WHERE owner = ? AND operation_key = ? AND status IS NOT NULL', [owner, key]);
        return rows[0] ? operation(rows[0]) : undefined;
    });
}

async function getAllAds(): Promise<Ad[]> {
    return withAdsConnection(async connection => {
        const [rows] = await connection.query<RowDataPacket[]>('SELECT id, title, publisher, content, url, price, clicks, videos, version FROM ads_business_ads ORDER BY ranking_score DESC, id ASC');
        return rows.map(ad);
    });
}

async function create(data: AdInput): Promise<Ad> {
    const current = transaction();
    const value: Ad = { id: randomUUID(), title: data.title, publisher: data.publisher, content: data.content,
        url: data.url, price: Number(data.price), clicks: 0, videos: data.videos || [], version: 1 };
    const doc = await recall.lock(current.connection, value.id, true);
    await current.connection.execute('INSERT INTO ads_business_ads (id, title, publisher, content, url, price, videos) VALUES (?, ?, ?, ?, ?, ?, ?)',
        [value.id, value.title, value.publisher, value.content, value.url, storedPrice(data.price), JSON.stringify(value.videos)]);
    current.receipt = await recall.emit(current.connection, doc, data);
    const result = await recall.decorate(current.connection,value,doc);
    if(current.receipt) result.recall_receipt=current.receipt;
    current.changes.push({ before: null, after: result });
    return result;
}

async function lockedAd(connection: PoolConnection, id: string): Promise<Ad | null> {
    const [rows] = await connection.execute<RowDataPacket[]>('SELECT * FROM ads_business_ads WHERE id = ? FOR UPDATE', [id]);
    return rows[0] ? ad(rows[0]) : null;
}

async function update(id: string, data: AdInput): Promise<Ad | null> {
    const current = transaction();
    const doc = await recall.lock(current.connection,id);
    const before = await recall.decorate(current.connection, await lockedAd(current.connection, id), doc);
    if (!before) return null;
    if (before.version !== data.version) throw conflict('广告已被其他运营修改，请刷新列表后重新编辑');
    const after: Ad = { ...before, title: data.title, publisher: data.publisher, content: data.content,
        url: data.url, price: Number(data.price), videos: data.videos ?? before.videos, version: before.version + 1 };
    const [updated] = await current.connection.execute<ResultSetHeader>('UPDATE ads_business_ads SET title = ?, publisher = ?, content = ?, url = ?, price = ?, videos = ?, version = version + 1 WHERE id = ? AND version = ?',
        [after.title, after.publisher, after.content, after.url, storedPrice(data.price), JSON.stringify(after.videos), id, before.version]);
    if (updated.affectedRows !== 1) throw conflict('广告版本冲突');
    current.receipt = await recall.emit(current.connection,doc,data);
    const result = await recall.decorate(current.connection,after,doc);
    if(current.receipt) result.recall_receipt=current.receipt;
    current.changes.push({ before, after: result });
    return result;
}

async function remove(id: string): Promise<boolean> {
    const current = transaction();
    const doc = await recall.lock(current.connection,id);
    const before = await recall.decorate(current.connection, await lockedAd(current.connection, id), doc);
    if (!before) return false;
    await current.connection.execute('DELETE FROM ads_business_ads WHERE id = ?', [id]);
    current.receipt = await recall.emit(current.connection,doc,{},true);
    current.changes.push({ before, after: null });
    return true;
}

async function incrementClicks(id: string, includeReceipt=false): Promise<any> {
    return withAdsConnection(async connection => {
        await connection.beginTransaction();
        try {
            const doc = await recall.lock(connection,id);
            const [result] = await connection.execute<ResultSetHeader>('UPDATE ads_business_ads SET clicks = clicks + 1 WHERE id = ?', [id]);
            if (!result.affectedRows) { await connection.rollback(); return null; }
            const [rows] = await connection.execute<RowDataPacket[]>('SELECT clicks FROM ads_business_ads WHERE id = ?', [id]);
            const receipt = await recall.emit(connection,doc);
            await connection.commit();
            return includeReceipt ? {clicks:Number(rows[0].clicks), ...(receipt?{recall_receipt:receipt}:{})} : Number(rows[0].clicks);
        } catch (error) { await connection.rollback(); throw error; }
    });
}

async function initialize(): Promise<void> {
    await withAdsConnection(async connection => {
        await connection.query('SELECT id, version FROM ads_business_ads LIMIT 0');
        await connection.query('SELECT owner FROM ads_business_operations LIMIT 0');
        await connection.query('SELECT id FROM ads_business_audit LIMIT 0');
        if(enabled()) {
            await connection.query('SELECT doc_id FROM ads_business_recall_docs LIMIT 0');
            const [missing] = await connection.query<RowDataPacket[]>('SELECT a.id FROM ads_business_ads a LEFT JOIN ads_business_recall_docs d ON d.ad_id=a.id WHERE d.doc_id IS NULL LIMIT 1');
            if(missing.length) throw Object.assign(new Error('recall_migration_required'),{status:503});
        }
    });
}

export default { executeOperation, getOperation, getAllAds, create, update, remove, incrementClicks,
    initialize, close: closeAdsDatabase };
