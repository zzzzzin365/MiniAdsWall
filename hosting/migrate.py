"""Explicit, idempotent initial migration. Never run migrations in every API worker."""
from dotenv import load_dotenv

def main():
    load_dotenv()
    from .config import repository
    repo=repository()
    try: repo.migrate(); print('Hosting schema version 1 ready')
    finally: repo.engine.dispose()
if __name__=='__main__': main()
