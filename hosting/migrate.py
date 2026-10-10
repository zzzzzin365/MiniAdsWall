"""Explicit upgrades: python -m hosting.migrate [up|status] [--target N]."""
import argparse
import json
import os
from sqlalchemy import create_engine
from dotenv import load_dotenv
from .migrations import upgrade, status, MigrationError

def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['up','status'],nargs='?',default='up')
    parser.add_argument('--target',type=int)
    parser.add_argument('--lock-timeout',type=int,default=30)
    args=parser.parse_args()
    if args.command=='status' and args.target is not None: parser.error('status does not accept --target')
    if not 0<=args.lock_timeout<=60: parser.error('--lock-timeout must be 0..60')
    url=os.environ.get('DATABASE_URL','')
    if not url.startswith('mysql+pymysql://'): parser.error('DATABASE_URL must use mysql+pymysql')
    engine=create_engine(url,pool_pre_ping=True,connect_args={'connect_timeout':3,'read_timeout':60,'write_timeout':60})
    try:
        result=status(engine) if args.command=='status' else upgrade(engine,target=args.target,lock_timeout=args.lock_timeout)
        print(json.dumps(result))
    except Exception as exc:
        parser.exit(1, (str(exc) if isinstance(exc,MigrationError) else type(exc).__name__)+'\n')
    finally: engine.dispose()
if __name__=='__main__': main()
