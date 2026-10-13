"""Submit once, or query a saved Seedance task. Keys are never printed."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from dotenv import load_dotenv
from fastapi import HTTPException

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from api.video import create_task, get_task, VideoInput

parser=argparse.ArgumentParser()
sub=parser.add_subparsers(dest='command',required=True)
create=sub.add_parser('create')
create.add_argument('--prompt',required=True)
create.add_argument('--image-url')
create.add_argument('--resolution',choices=['480p','720p','1080p'],default='480p')
create.add_argument('--duration',type=int,default=5)
create.add_argument('--ratio',default='9:16')
create.add_argument('--receipt',type=Path,required=True,help='Save task ID to avoid resubmission')
query=sub.add_parser('query');query.add_argument('task_id')
args=parser.parse_args()
load_dotenv(ROOT/'.env')

async def main():
    try:
        if args.command=='create':
            # Verify a writable receipt location before creating a billable task.
            args.receipt.parent.mkdir(parents=True,exist_ok=True)
            with args.receipt.open('x') as file:
                file.write('{"status":"submitting"}')
            data=await create_task(VideoInput(prompt=args.prompt,image_url=args.image_url,
                resolution=args.resolution,duration=args.duration,ratio=args.ratio))
            args.receipt.write_text(json.dumps(data,ensure_ascii=False,indent=2))
        else:
            data=await get_task(args.task_id)
        print(json.dumps(data,ensure_ascii=False,indent=2))
    except HTTPException as exc:
        print(json.dumps({'status':exc.status_code,'detail':exc.detail},ensure_ascii=False))
        return 1
    return 0

if __name__=='__main__':
    sys.exit(asyncio.run(main()))
