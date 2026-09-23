"""Bounded content-addressed objects. S3 in production, local directory in tests/dev."""
import hashlib
import os
from pathlib import Path

MAX_OBJECT = 50 * 1024 * 1024

def preview(text, limit=8192):
    return text.encode('utf-8', errors='replace')[:limit].decode('utf-8', errors='ignore')

class Objects:
    def __init__(self, root=None, bucket=None, endpoint=None):
        self.bucket = bucket
        if bucket:
            import boto3
            self.client = boto3.client('s3', endpoint_url=endpoint)
        else:
            self.root = Path(root or 'data/hosting-objects').resolve()
            self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content):
        data = content.encode() if isinstance(content, str) else content
        if len(data) > MAX_OBJECT:
            raise ValueError('object_limit_exceeded')
        key = hashlib.sha256(data).hexdigest()
        if self.bucket:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                   Metadata={'sha256': key}, ContentType='application/octet-stream')
        else:
            import tempfile
            fd, temp = tempfile.mkstemp(dir=self.root, prefix='.pending-')
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(data); f.flush(); os.fsync(f.fileno())
                os.replace(temp, self.root / key)
            finally:
                if os.path.exists(temp): os.unlink(temp)
        return key

    def get(self, key):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('invalid_object_key')
        if self.bucket:
            body = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
            try: data = body.read(MAX_OBJECT + 1)
            finally: body.close()
        else:
            with (self.root / key).open('rb') as f: data = f.read(MAX_OBJECT + 1)
        if len(data) > MAX_OBJECT or hashlib.sha256(data).hexdigest() != key:
            raise ValueError('object_integrity_error')
        return data
