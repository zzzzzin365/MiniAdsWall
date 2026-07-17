import { checkFileUpload, uploadChunk, mergeChunks } from '../api';
import { UploadProgress } from '../types';
import HashWorker from '../workers/hashWorker?worker';
import type { HashWorkerMessage, HashWorkerResult } from '../workers/hashWorker';
import SparkMD5 from 'spark-md5';

const CHUNK_SIZE = 2 * 1024 * 1024; // 2MB per chunk
const MAX_CONCURRENCY = 3;

type ProgressCallback = (progress: UploadProgress) => void;

function computeFileHashInWorker(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const worker = new HashWorker();
        worker.onmessage = (e: MessageEvent<HashWorkerResult>) => {
            if (e.data.type === 'FILE_HASH_RESULT') {
                worker.terminate();
                if (e.data.hash) {
                    resolve(e.data.hash);
                } else {
                    reject(new Error('Hash computation failed'));
                }
            }
        };
        worker.onerror = (err) => {
            worker.terminate();
            reject(err);
        };
        worker.postMessage({
            type: 'FILE_HASH',
            file,
            chunkSize: CHUNK_SIZE
        } as HashWorkerMessage);
    });
}

function computeChunkMd5(chunk: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            if (e.target?.result) {
                const spark = new SparkMD5.ArrayBuffer();
                spark.append(e.target.result as ArrayBuffer);
                resolve(spark.end());
            } else {
                reject(new Error('Failed to read chunk'));
            }
        };
        reader.onerror = () => reject(new Error('FileReader error'));
        reader.readAsArrayBuffer(chunk);
    });
}

/**
 * 受控并发队列：限制同时上传的分片数量，避免浏览器资源挤占
 */
async function concurrencyPool<T>(
    tasks: (() => Promise<T>)[],
    maxConcurrency: number
): Promise<T[]> {
    const results: T[] = new Array(tasks.length);
    let nextIndex = 0;

    async function worker() {
        while (nextIndex < tasks.length) {
            const currentIndex = nextIndex++;
            results[currentIndex] = await tasks[currentIndex]();
        }
    }

    const workers = Array.from(
        { length: Math.min(maxConcurrency, tasks.length) },
        () => worker()
    );
    await Promise.all(workers);
    return results;
}

/**
 * 分片上传主流程：
 * 1. 计算文件 MD5（Web Worker）
 * 2. 向后端查询秒传/断点续传状态
 * 3. 使用受控并发队列上传缺失分片
 * 4. 请求后端合并分片并校验最终文件 MD5
 */
export async function chunkedUploadFile(
    file: File,
    onProgress: ProgressCallback
): Promise<string> {
    onProgress({ phase: 'hashing', percent: 0, message: '正在计算文件指纹...' });

    const fileMd5 = await computeFileHashInWorker(file);

    onProgress({ phase: 'hashing', percent: 100, message: '文件指纹计算完成' });

    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

    onProgress({ phase: 'checking', percent: 0, message: '正在检查文件状态...' });

    const checkResult = await checkFileUpload(fileMd5, file.name, totalChunks, file.size);

    if (checkResult.exists && checkResult.url) {
        onProgress({ phase: 'done', percent: 100, message: '秒传成功' });
        return checkResult.url;
    }

    const uploadedSet = new Set(checkResult.uploadedChunks || []);
    const chunksToUpload: number[] = [];
    for (let i = 0; i < totalChunks; i++) {
        if (!uploadedSet.has(i)) {
            chunksToUpload.push(i);
        }
    }

    if (uploadedSet.size > 0) {
        onProgress({
            phase: 'uploading',
            percent: Math.round((uploadedSet.size / totalChunks) * 100),
            message: `已有 ${uploadedSet.size}/${totalChunks} 个分片，断点续传中...`
        });
    }

    let completedCount = uploadedSet.size;

    const uploadTasks = chunksToUpload.map((chunkIndex) => {
        return async () => {
            const start = chunkIndex * CHUNK_SIZE;
            const end = Math.min(start + CHUNK_SIZE, file.size);
            const chunk = file.slice(start, end);

            const chunkMd5 = await computeChunkMd5(chunk);

            await uploadChunk(chunk, fileMd5, chunkIndex, chunkMd5);

            completedCount++;
            onProgress({
                phase: 'uploading',
                percent: Math.round((completedCount / totalChunks) * 100),
                message: `上传中 ${completedCount}/${totalChunks}`
            });
        };
    });

    await concurrencyPool(uploadTasks, MAX_CONCURRENCY);

    onProgress({ phase: 'merging', percent: 95, message: '正在合并文件...' });

    const mergeResult = await mergeChunks(fileMd5, file.name, totalChunks, file.size);

    if (!mergeResult.success) {
        onProgress({ phase: 'error', percent: 0, message: '文件合并失败' });
        throw new Error('File merge failed');
    }

    onProgress({ phase: 'done', percent: 100, message: '上传完成' });

    return mergeResult.url;
}
