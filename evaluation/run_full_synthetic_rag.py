"""Full 36M read-only float corpus: exact gold vs binary coarse/float refine.

Eight distinct queries in one batch per scan, NOT single-query P95.
No semantic-recall claims. Binary index is a separate derived directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import numpy as np

def save(p,v):p.write_text(json.dumps(v,indent=2))
def main(a):
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    root=Path(a.source);manifest=json.loads((root/'manifest.json').read_text())
    shards=manifest['shards'];count=sum(s['count'] for s in shards)
    assert count==36_000_000 and all((root/s['file']).stat().st_size==s['count']*384*4 for s in shards)
    bits=out/'binary';bits.mkdir()
    if shutil.disk_usage(out).free < count*48+2*1024**3:raise RuntimeError('insufficient space')
    rng=np.random.default_rng(20261014);queries=[];anchors=np.linspace(0,count-1,4,dtype=int)
    for ident in anchors:
        s=next(s for s in shards if s['start']<=ident<s['start']+s['count'])
        m=np.memmap(root/s['file'],dtype='float32',mode='r',shape=(s['count'],384))
        queries.append(m[ident-s['start']].copy()+rng.normal(0,.025,384));del m
    queries.extend(rng.normal(0,1,(4,384)))
    q=np.asarray(queries,dtype='float32');q/=np.linalg.norm(q,axis=1,keepdims=True)
    np.savez(out/'queries.npz',queries=q)
    save(out/'manifest.json',{'status':'running','synthetic':True,'source':str(root),'vectors':count,'dimension':384,
        'query_count':8,'seed':20261014,'source_manifest_sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
        'query_sha256':hashlib.sha256(q.tobytes()).hexdigest(),'batch_size':a.batch,
        'query_types':['four perturbed source anchors','four independent random queries'],'limits':['batch scan only, not single-query P95 or concurrency','synthetic random vectors, not semantic recall']})
    t=time.perf_counter();binary_meta=[]
    for n,s in enumerate(shards):
        m=np.memmap(root/s['file'],dtype='float32',mode='r',shape=(s['count'],384));dest=bits/(s['file']+'.bits')
        h=hashlib.sha256()
        with dest.open('wb') as f:
            for start in range(0,len(m),a.batch):
                b=np.packbits(m[start:start+a.batch]>=0,axis=1).tobytes();f.write(b);h.update(b)
            f.flush();os.fsync(f.fileno())
        binary_meta.append({'file':dest.name,'count':s['count'],'start':s['start'],'bytes':dest.stat().st_size,'sha256':h.hexdigest()})
        del m
        if (n+1)%6==0:print('binary built',n+1,'/36',flush=True)
    build=time.perf_counter()-t;save(out/'binary-manifest.json',binary_meta)
    best=np.full((8,5),-np.inf,dtype='float32');best_ids=np.full((8,5),-1,dtype='int64')
    t=time.perf_counter()
    for n,s in enumerate(shards):
        m=np.memmap(root/s['file'],dtype='float32',mode='r',shape=(s['count'],384))
        for start in range(0,len(m),a.batch):
            scores=m[start:start+a.batch]@q.T
            for j in range(8):
                ix=np.argpartition(-scores[:,j],4)[:5]
                sc=np.concatenate([best[j],scores[ix,j]]);ii=np.concatenate([best_ids[j],ix+start+s['start']])
                keep=np.argsort(-sc,kind='stable')[:5];best[j]=sc[keep];best_ids[j]=ii[keep]
        del m
        if (n+1)%6==0:print('exact scanned',n+1,'/36',flush=True)
    exact_seconds=time.perf_counter()-t;save(out/'exact-gold.json',{'ids':best_ids.tolist(),'scores':best.tolist(),'batch_seconds':exact_seconds})
    maxk=10000;coarse=np.full((8,maxk),999,dtype='int16');coarse_ids=np.full((8,maxk),-1,dtype='int64')
    pop=np.array([int(i).bit_count() for i in range(256)],dtype='uint8');qb=np.packbits(q>=0,axis=1)
    t=time.perf_counter()
    for n,s in enumerate(binary_meta):
        m=np.memmap(bits/s['file'],dtype='uint8',mode='r',shape=(s['count'],48))
        for start in range(0,len(m),a.batch):
            block=m[start:start+a.batch]
            for j in range(8):
                dist=pop[np.bitwise_xor(block,qb[j])].sum(axis=1).astype('int16')
                k=min(maxk,len(dist));ix=np.argpartition(dist,k-1)[:k]
                dd=np.concatenate([coarse[j],dist[ix]]);ii=np.concatenate([coarse_ids[j],ix+start+s['start']])
                keep=np.lexsort((ii,dd))[:maxk];coarse[j]=dd[keep];coarse_ids[j]=ii[keep]
        del block,m
        if (n+1)%6==0:print('binary scanned',n+1,'/36',flush=True)
    coarse_seconds=time.perf_counter()-t
    np.savez(out/'coarse-candidates.npz',ids=coarse_ids,distances=coarse)
    arms={}
    for k in [100,1000,10000]:
        t=time.perf_counter();ranking=[]
        for j in range(8):
            wanted=coarse_ids[j,:k];sc=np.empty(len(wanted),dtype='float32')
            for s in shards:
                mask=(wanted>=s['start'])&(wanted<s['start']+s['count'])
                if mask.any():
                    m=np.memmap(root/s['file'],dtype='float32',mode='r',shape=(s['count'],384));sc[mask]=m[wanted[mask]-s['start']]@q[j];del m
            ranking.append(wanted[np.argsort(-sc,kind='stable')[:5]].tolist())
        seconds=time.perf_counter()-t
        recalls=[len(set(ranking[j])&set(best_ids[j]))/5 for j in range(8)]
        arms[str(k)]={'refine_candidates':k,'refine_batch_seconds':seconds,'total_batch_seconds':coarse_seconds+seconds,
            'neighbor_recall_at5':float(np.mean(recalls)),'per_query_recall':recalls,'top5_ids':ranking,
            'exact_vs_binary_batch_speedup':exact_seconds/(coarse_seconds+seconds)}
    report={'status':'completed','synthetic':True,'actual_vectors_scanned_per_pass':count,'queries':8,
        'source_float32_bytes':count*384*4,'binary_bytes':sum(s['bytes'] for s in binary_meta),
        'binary_build_seconds':build,'exact_batch_seconds':exact_seconds,'binary_coarse_batch_seconds':coarse_seconds,'arms':arms,
        'limits':['Eight queries in shared full-scan batch: no per-request P95, QPS or concurrency proof.',
        'Coarse scan shared among refinement cutoffs; these are budget ablations, not three independent scans.',
        'Float32 source retained; bit storage shrinks vector values 32x, not entire RAG footprint.',
        'Index-building cost and warm/cold OS page-cache differences matter; one run in fixed order.',
        'Binary Hamming ties within block use argpartition; not an exact cosine-preserving representation.',
        'No business knowledge or private material touched; random-vector neighbors only.']}
    save(out/'summary.json',report)
    manifest_out=json.loads((out/'manifest.json').read_text());manifest_out['status']='completed';save(out/'manifest.json',manifest_out)
    print(json.dumps(report),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',default='data/benchmarks/synthetic-36m-384-f32')
    p.add_argument('--output',required=True);p.add_argument('--batch',type=int,default=50000);main(p.parse_args())
