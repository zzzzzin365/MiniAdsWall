import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import config from '../config';
import { ChunkCheckRequest, ChunkMergeRequest } from '../types';

const CHUNK_DIR = path.join(config.UPLOAD_DIR, 'chunks');

function ensureDir(dir: string): void {
    if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
    }
}

ensureDir(config.UPLOAD_DIR);
ensureDir(CHUNK_DIR);

function getChunkDir(fileMd5: string): string {
    return path.join(CHUNK_DIR, fileMd5);
}

function getChunkPath(fileMd5: string, chunkIndex: number): string {
    return path.join(getChunkDir(fileMd5), `chunk_${chunkIndex}`);
}

function computeFileMd5(filePath: string): Promise<string> {
    return new Promise((resolve, reject) => {
        const hash = crypto.createHash('md5');
        const stream = fs.createReadStream(filePath);
        stream.on('data', (data) => hash.update(data));
        stream.on('end', () => resolve(hash.digest('hex')));
        stream.on('error', reject);
    });
}

function computeBufferMd5(buffer: Buffer): string {
    return crypto.createHash('md5').update(buffer).digest('hex');
}

/**
 * 秒传 & 断点续传检查
 * 通过文件 MD5 判断：已存在则秒传；部分上传则返回已完成分片索引
 */
function checkFile(data: ChunkCheckRequest): { exists: boolean; url?: string; uploadedChunks?: number[] } {
    const { fileMd5, fileName } = data;

    const existingFiles = fs.readdirSync(config.UPLOAD_DIR);
    for (const f of existingFiles) {
        if (f.startsWith(fileMd5 + '_')) {
            return {
                exists: true,
                url: `http://localhost:${config.PORT}/${f}`
            };
        }
    }

    const chunkDir = getChunkDir(fileMd5);
    if (!fs.existsSync(chunkDir)) {
        return { exists: false, uploadedChunks: [] };
    }

    const uploadedChunks: number[] = [];
    const files = fs.readdirSync(chunkDir);
    for (const f of files) {
        const match = f.match(/^chunk_(\d+)$/);
        if (match) {
            uploadedChunks.push(parseInt(match[1]));
        }
    }
    uploadedChunks.sort((a, b) => a - b);

    return { exists: false, uploadedChunks };
}

/**
 * 接收单个分片并做 MD5 校验 + 幂等处理
 */
function saveChunk(
    fileMd5: string,
    chunkIndex: number,
    chunkMd5: string,
    chunkBuffer: Buffer
): { success: boolean; error?: string } {
    const chunkDir = getChunkDir(fileMd5);
    ensureDir(chunkDir);
    const chunkPath = getChunkPath(fileMd5, chunkIndex);

    if (fs.existsSync(chunkPath)) {
        return { success: true };
    }

    const actualMd5 = computeBufferMd5(chunkBuffer);
    if (actualMd5 !== chunkMd5) {
        return {
            success: false,
            error: `Chunk ${chunkIndex} MD5 mismatch: expected ${chunkMd5}, got ${actualMd5}`
        };
    }

    fs.writeFileSync(chunkPath, chunkBuffer);
    return { success: true };
}

/**
 * 按索引顺序合并分片，合并后校验整文件 MD5
 */
async function mergeChunks(data: ChunkMergeRequest): Promise<{ success: boolean; filename?: string; url?: string; error?: string }> {
    const { fileMd5, fileName, totalChunks } = data;
    const chunkDir = getChunkDir(fileMd5);

    if (!fs.existsSync(chunkDir)) {
        return { success: false, error: 'Chunk directory not found' };
    }

    for (let i = 0; i < totalChunks; i++) {
        if (!fs.existsSync(getChunkPath(fileMd5, i))) {
            return { success: false, error: `Missing chunk ${i}` };
        }
    }

    const ext = path.extname(fileName);
    const finalName = `${fileMd5}_${Date.now()}${ext}`;
    const finalPath = path.join(config.UPLOAD_DIR, finalName);

    const writeStream = fs.createWriteStream(finalPath);
    for (let i = 0; i < totalChunks; i++) {
        const chunkPath = getChunkPath(fileMd5, i);
        const chunkData = fs.readFileSync(chunkPath);
        writeStream.write(chunkData);
    }

    await new Promise<void>((resolve, reject) => {
        writeStream.on('finish', resolve);
        writeStream.on('error', reject);
        writeStream.end();
    });

    const mergedMd5 = await computeFileMd5(finalPath);
    if (mergedMd5 !== fileMd5) {
        fs.unlinkSync(finalPath);
        return {
            success: false,
            error: `Final file MD5 mismatch: expected ${fileMd5}, got ${mergedMd5}`
        };
    }

    fs.rmSync(chunkDir, { recursive: true, force: true });

    return {
        success: true,
        filename: finalName,
        url: `http://localhost:${config.PORT}/${finalName}`
    };
}

export default {
    checkFile,
    saveChunk,
    mergeChunks
};
