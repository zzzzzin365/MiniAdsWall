"""Read-only HTTP pagination benchmark; never counts model calls as HTTP throughput.
Needs a pre-seeded session with history. No synthetic production-scale claims.
"""
import argparse
import asyncio
import json
import os
import platform
import statistics
import time
import httpx

def percentile(values,p): return sorted(values)[min(len(values)-1,int((len(values)-1)*p))]
async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8002')
    parser.add_argument('--subject',required=True)
    parser.add_argument('--session',required=True)
    parser.add_argument('--requests',type=int,default=1000)
    parser.add_argument('--concurrency',type=int,default=50)
    parser.add_argument('--qps',type=float,default=100)
    parser.add_argument('--output',default='hosting-query-benchmark.json')
    args=parser.parse_args()
    if args.requests<1 or args.concurrency<1 or args.qps<=0: parser.error('positive counts required')
    headers={'Authorization':'Bearer '+os.environ['AGENT_SERVICE_TOKEN']}
    async with httpx.AsyncClient(base_url=args.url,timeout=10,headers=headers,trust_env=False,limits=httpx.Limits(max_connections=args.concurrency,max_keepalive_connections=args.concurrency)) as client:
        auth=await client.post('/auth/session',json={'subject':args.subject}); auth.raise_for_status()
        client.headers['X-Agent-Session']=auth.json()['session_token']
        results=[]; semaphore=asyncio.Semaphore(args.concurrency); started=time.monotonic()
        async def request(i):
            scheduled=started+i/args.qps
            await asyncio.sleep(max(0,scheduled-time.monotonic()))
            async with semaphore:
                before=time.monotonic(); path='/sessions?limit=20' if i%2==0 else f'/sessions/{args.session}/history?limit=50'
                try:
                    response=await client.get(path); ok=response.status_code==200
                except httpx.HTTPError: ok=False
                results.append({'route':'sessions' if i%2==0 else 'history','ms':(time.monotonic()-before)*1000,'load_delay_ms':max(0,before-scheduled)*1000,'ok':ok})
        # Bound load-generator tasks as well as connections.
        for offset in range(0,args.requests,10000):
            await asyncio.gather(*(request(i) for i in range(offset,min(offset+10000,args.requests))))
        report={'kind':'HTTP query benchmark; excludes model and SSE','platform':platform.platform(),
                'elapsed_seconds':time.monotonic()-started,'requests':args.requests,'concurrency_limit':args.concurrency,
                'target_qps':args.qps,'database_scale':'must be supplied with seeded dataset evidence','routes':{}}
        for route in ['sessions','history']:
            samples=[r for r in results if r['route']==route]; values=[r['ms'] for r in samples]
            if values: report['routes'][route]={'p50_ms':percentile(values,.5),'p95_ms':percentile(values,.95),'p99_ms':percentile(values,.99),'error_rate':sum(not r['ok'] for r in samples)/len(samples),'load_delay_p95_ms':percentile([r['load_delay_ms'] for r in samples],.95)}
        with open(args.output,'w') as f: json.dump(report,f,ensure_ascii=False,indent=2)
        print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': asyncio.run(main())
