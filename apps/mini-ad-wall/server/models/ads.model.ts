import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import config from '../config';
import { Ad, AdInput } from '../types';

let ads: Ad[] = [];
interface Operation { fingerprint: string; status: number; body: any; before: Ad[]; after: Ad[]; createdAt: string }
let operations: Record<string, Operation> = Object.create(null);
let inOperation = false;

// ============ 点击计数性能优化 ============
// 使用内存缓冲区累积点击，定时批量写入文件，避免每次点击都触发 I/O
const clickBuffer: Map<string, number> = new Map();
let flushTimer: NodeJS.Timeout | null = null;
const FLUSH_INTERVAL = 5000; // 5秒批量写入一次
// ==========================================

function loadData(): void {
    try {
        if (fs.existsSync(config.DATA_FILE)) {
            const data = fs.readFileSync(config.DATA_FILE, 'utf8');
            const stored = JSON.parse(data);
            ads = Array.isArray(stored) ? stored : stored.ads;
            operations = Object.assign(Object.create(null), Array.isArray(stored) ? {} : stored.operations);
            if (!Array.isArray(ads)) throw new Error('Invalid ads storage');
        } else {
            ads = [...config.DEFAULT_ADS];
            saveData();
        }
    } catch (err) {
        console.error("Error loading data:", err);
        throw err;
    }
}

function saveData(): void {
    if (inOperation) return;
    fs.mkdirSync(path.dirname(config.DATA_FILE), { recursive: true });
    const temporary = `${config.DATA_FILE}.${process.pid}.tmp`;
    const fd = fs.openSync(temporary, 'w', 0o600);
    try {
        fs.writeFileSync(fd, JSON.stringify({ ads, operations }, null, 2));
        fs.fsyncSync(fd);
    } finally {
        fs.closeSync(fd);
    }
    fs.renameSync(temporary, config.DATA_FILE);
}

// Synchronous transaction: ad changes and their result share one atomic file replacement.
// Single Koa process only; move to database transactions before running multiple replicas.
function executeOperation(key: string, fingerprint: string, apply: () => { status: number; body: any }): Operation {
    const existing = operations[key];
    if (existing) {
        if (existing.fingerprint !== fingerprint) throw Object.assign(new Error('操作标识已用于不同请求'), { status: 409 });
        return existing;
    }
    const before = JSON.parse(JSON.stringify(ads));
    try {
        inOperation = true;
        const result = apply();
        const record = { ...result, fingerprint, before, after: JSON.parse(JSON.stringify(ads)), createdAt: new Date().toISOString() };
        operations[key] = record;
        inOperation = false;
        saveData();
        return record;
    } catch (error) {
        ads = before;
        delete operations[key];
        throw error;
    } finally {
        inOperation = false;
    }
}

function getOperation(key: string): Operation | undefined { return operations[key]; }

function getAllAds(): Ad[] {
    return [...ads];
}

function findById(id: string): Ad | null {
    return ads.find(a => a.id === id) || null;
}

function findIndexById(id: string): number {
    return ads.findIndex(a => a.id === id);
}

function create(adData: AdInput): Ad {
    const newAd: Ad = {
        id: randomUUID(),
        title: adData.title,
        publisher: adData.publisher,
        content: adData.content,
        url: adData.url,
        price: parseFloat(String(adData.price)),
        clicks: 0,
        videos: adData.videos || []
    };
    ads.push(newAd);
    saveData();
    return newAd;
}

function update(id: string, updateData: AdInput): Ad | null {
    const index = findIndexById(id);
    if (index === -1) {
        return null;
    }
    const updatedAd: Ad = {
        ...ads[index],
        title: updateData.title,
        publisher: updateData.publisher,
        content: updateData.content,
        url: updateData.url,
        price: parseFloat(String(updateData.price)),
        videos: updateData.videos !== undefined ? updateData.videos : ads[index].videos
    };
    ads[index] = updatedAd;
    saveData();
    return updatedAd;
}

function remove(id: string): boolean {
    const initialLength = ads.length;
    ads = ads.filter(a => a.id !== id);
    if (ads.length === initialLength) {
        return false;
    }
    saveData();
    return true;
}

/**
 * 批量写入缓冲区中的点击数据到文件
 * 性能优化：将多次点击合并为一次文件写入
 */
function flushClicks(): void {
    if (clickBuffer.size === 0) {
        flushTimer = null;
        return;
    }

    console.log(`[ClickBuffer] Flushing ${clickBuffer.size} ad(s) click data to disk`);
    
    // 将缓冲区的点击数同步到 ads 数组（实际上已经在 incrementClicks 中同步了）
    // 这里主要是写入文件
    saveData();
    clickBuffer.clear();
    flushTimer = null;
}

/**
 * 增加广告点击数（性能优化版）
 * - 点击数立即在内存中更新，保证读取时数据正确
 * - 文件写入延迟批量执行，减少 I/O 操作
 */
function incrementClicks(id: string): number | null {
    const ad = findById(id);
    if (!ad) {
        return null;
    }
    
    // 立即更新内存中的点击数
    ad.clicks = (ad.clicks || 0) + 1;
    
    // 记录到缓冲区（用于追踪哪些广告有变更）
    const buffered = clickBuffer.get(id) || 0;
    clickBuffer.set(id, buffered + 1);
    
    // 启动延迟写入定时器（如果还没启动）
    if (!flushTimer) {
        flushTimer = setTimeout(flushClicks, FLUSH_INTERVAL);
        console.log(`[ClickBuffer] Timer started, will flush in ${FLUSH_INTERVAL}ms`);
    }
    
    return ad.clicks;
}

/**
 * 强制立即写入所有缓冲的点击数据
 * 用于服务关闭前确保数据不丢失
 */
function forceFlush(): void {
    if (flushTimer) {
        clearTimeout(flushTimer);
        flushTimer = null;
    }
    if (clickBuffer.size > 0) {
        console.log('[ClickBuffer] Force flushing before shutdown...');
        saveData();
        clickBuffer.clear();
    }
}

// 确保进程退出时数据不丢失
process.on('SIGINT', () => {
    forceFlush();
    process.exit(0);
});
process.on('SIGTERM', () => {
    forceFlush();
    process.exit(0);
});

loadData();

export default {
    executeOperation,
    getOperation,
    getAllAds,
    findById,
    findIndexById,
    create,
    update,
    remove,
    incrementClicks,
    forceFlush,      // 导出强制写入方法，用于优雅关闭
    flushClicks      // 导出手动触发写入方法
};
