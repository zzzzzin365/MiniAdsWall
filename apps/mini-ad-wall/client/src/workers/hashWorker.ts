import SparkMD5 from 'spark-md5';

export interface HashWorkerMessage {
    type: 'FILE_HASH' | 'CHUNK_HASH';
    file?: File;
    chunk?: Blob;
    chunkSize?: number;
}

export interface HashWorkerResult {
    type: 'FILE_HASH_RESULT' | 'CHUNK_HASH_RESULT' | 'PROGRESS';
    hash?: string;
    progress?: number;
}

const ctx = self as unknown as Worker;

function computeFileHash(file: File, chunkSize: number): void {
    const spark = new SparkMD5.ArrayBuffer();
    const reader = new FileReader();
    const totalChunks = Math.ceil(file.size / chunkSize);
    let currentChunk = 0;

    function readNext() {
        const start = currentChunk * chunkSize;
        const end = Math.min(start + chunkSize, file.size);
        reader.readAsArrayBuffer(file.slice(start, end));
    }

    reader.onload = (e) => {
        if (e.target?.result) {
            spark.append(e.target.result as ArrayBuffer);
        }
        currentChunk++;
        ctx.postMessage({
            type: 'PROGRESS',
            progress: Math.round((currentChunk / totalChunks) * 100)
        } as HashWorkerResult);

        if (currentChunk < totalChunks) {
            readNext();
        } else {
            ctx.postMessage({
                type: 'FILE_HASH_RESULT',
                hash: spark.end()
            } as HashWorkerResult);
        }
    };

    reader.onerror = () => {
        ctx.postMessage({ type: 'FILE_HASH_RESULT', hash: '' });
    };

    readNext();
}

function computeChunkHash(chunk: Blob): void {
    const reader = new FileReader();
    reader.onload = (e) => {
        if (e.target?.result) {
            const spark = new SparkMD5.ArrayBuffer();
            spark.append(e.target.result as ArrayBuffer);
            ctx.postMessage({
                type: 'CHUNK_HASH_RESULT',
                hash: spark.end()
            } as HashWorkerResult);
        }
    };
    reader.onerror = () => {
        ctx.postMessage({ type: 'CHUNK_HASH_RESULT', hash: '' });
    };
    reader.readAsArrayBuffer(chunk);
}

ctx.onmessage = (e: MessageEvent<HashWorkerMessage>) => {
    const { type, file, chunk, chunkSize } = e.data;
    if (type === 'FILE_HASH' && file) {
        computeFileHash(file, chunkSize || 2 * 1024 * 1024);
    } else if (type === 'CHUNK_HASH' && chunk) {
        computeChunkHash(chunk);
    }
};
