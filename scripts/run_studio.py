#!/usr/bin/env python3
"""Load local service configuration without printing secrets, run studio CLI."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
from dotenv import dotenv_values
ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'apps/mini-ad-wall/server'

def main():
    parser = argparse.ArgumentParser(description='商家创意工作台迁移/Worker')
    parser.add_argument('command', choices=['migrate', 'status', 'worker'])
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    env = {**os.environ, **{k:v for k,v in dotenv_values(ROOT/'.env').items() if v is not None},
           **{k:v for k,v in dotenv_values(SERVER/'.env.local').items() if v is not None}}
    node = '/opt/homebrew/opt/node@22/bin/node'
    if not Path(node).exists(): node = shutil.which('node')
    if not node or not env.get('ADS_MYSQL_URL'):
        raise SystemExit('需要Node22及server/.env.local中的ADS_MYSQL_URL')
    env['PATH'] = str(Path(node).parent) + os.pathsep + env.get('PATH','')
    cli = SERVER/'dist/scripts'/('studio-worker.js' if args.command=='worker' else 'migrate-studio.js')
    if not cli.exists(): raise SystemExit('请先 npm --prefix apps/mini-ad-wall/server run build')
    tail = ['--once'] if args.command=='worker' and args.once else [] if args.command=='worker' else ['up' if args.command=='migrate' else 'status']
    raise SystemExit(subprocess.call([node,str(cli),*tail],env=env,cwd=ROOT))
if __name__=='__main__':main()
