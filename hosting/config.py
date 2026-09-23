import os
from .objects import Objects
from .repository import Repository

def repository():
    return Repository(os.environ['DATABASE_URL'],
                      Objects(root=os.getenv('HOSTING_OBJECT_DIR'), bucket=os.getenv('HOSTING_S3_BUCKET'), endpoint=os.getenv('HOSTING_S3_ENDPOINT')),
                      os.environ['HOSTING_CURSOR_SECRET'])
