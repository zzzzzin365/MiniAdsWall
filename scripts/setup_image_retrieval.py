"""Download the pinned public image encoder once; requests never download it."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.image_embeddings import ChineseClip, MODEL_ID, MODEL_REVISION
if __name__ == '__main__':
    model = ChineseClip(local_files_only=False)
    print({'model': MODEL_ID, 'revision': MODEL_REVISION, 'dimension': model.dimension, 'device': 'cpu'})
