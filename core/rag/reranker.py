"""Optional local scalar cross-encoder; never downloads models at request time."""
import hashlib
import json
import threading
from pathlib import Path


class LocalReranker:
    def __init__(self, directory):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        root = Path(directory).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('reranker_directory_required')
        # Fingerprint every artifact, including weights, not merely the display name.
        checksum = hashlib.sha256()
        for path in sorted(p for p in root.rglob('*') if p.is_file()):
            checksum.update(str(path.relative_to(root)).encode())
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024*1024), b''):
                    checksum.update(block)
        self.revision = checksum.hexdigest()
        self.tokenizer = AutoTokenizer.from_pretrained(str(root), local_files_only=True, trust_remote_code=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(str(root), local_files_only=True,
            trust_remote_code=False, use_safetensors=True).to('cpu').eval()
        self.torch = torch
        self.lock = threading.Lock()
        config_limit = int(getattr(self.model.config, 'max_position_embeddings', 512))
        self.max_tokens = min(512, config_limit, self.tokenizer.model_max_length)

    def rank(self, query, rows, k):
        if not rows:
            return []
        with self.lock:
            encoded = self.tokenizer([[query, c['content']] for c in rows], padding=True,
                                     truncation=False, return_tensors='pt')
            if encoded['input_ids'].shape[1] > self.max_tokens:
                raise ValueError('reranker_input_exceeds_window')
            with self.torch.inference_mode():
                logits = self.model(**encoded).logits
            if logits.ndim != 2 or logits.shape[1] != 1 or not self.torch.isfinite(logits).all():
                raise ValueError('reranker_requires_finite_scalar_scores')
            scores = logits[:, 0].tolist()
            order = sorted(range(len(rows)), key=lambda i: (-scores[i], rows[i]['id']))
            return [rows[i] for i in order[:k]]
