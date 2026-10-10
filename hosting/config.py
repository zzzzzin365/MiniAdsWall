import os
from .objects import Objects
from .repository import Repository
from .reliability import load_reliability
from marketing.config import load_marketing
from .migrations import require_runtime_schema

def repository():
    load_reliability()
    load_marketing()
    repo = Repository(os.environ['DATABASE_URL'],
                      Objects(root=os.getenv('HOSTING_OBJECT_DIR'), bucket=os.getenv('HOSTING_S3_BUCKET'), endpoint=os.getenv('HOSTING_S3_ENDPOINT')),
                      os.environ['HOSTING_CURSOR_SECRET'])
    try: require_runtime_schema(repo.engine)
    except BaseException:
        repo.engine.dispose()
        raise
    return repo
