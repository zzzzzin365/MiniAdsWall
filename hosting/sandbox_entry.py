"""Runs INSIDE the untrusted-tool container. Container limits remain the security boundary."""
import base64
import io
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tarfile
import time

OUTPUT_LIMIT=10*1024*1024
SNAPSHOT_LIMIT=32*1024*1024

def unpack(encoded):
    raw=base64.b64decode(encoded,validate=True)
    if len(raw)>SNAPSHOT_LIMIT: raise ValueError('snapshot_too_large')
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        total=0
        for i,member in enumerate(archive):
            target=Path('/workspace',member.name)
            if i>10000 or member.name.startswith('/') or '..' in Path(member.name).parts or not (member.isfile() or member.isdir()):
                raise ValueError('unsafe_snapshot')
            total+=member.size
            if total>SNAPSHOT_LIMIT: raise ValueError('snapshot_too_large')
            if member.isdir(): target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as source, target.open('wb') as dest:
                    while chunk:=source.read(65536): dest.write(chunk)
                target.chmod(member.mode & 0o777)

def pack():
    files=[]; size=0
    for path in Path('/workspace').rglob('*'):
        if path.is_symlink(): raise ValueError('symlink_in_snapshot')
        if path.is_file():
            size+=path.stat().st_size
            if size>SNAPSHOT_LIMIT or len(files)>10000: raise ValueError('snapshot_too_large')
            files.append(path)
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w') as archive:
        for path in files: archive.add(path,arcname=str(path.relative_to('/workspace')),recursive=False)
    if len(buf.getvalue())>SNAPSHOT_LIMIT: raise ValueError('snapshot_too_large')
    return base64.b64encode(buf.getvalue()).decode()

def main():
    data=sys.stdin.buffer.read(48*1024*1024+1)
    if len(data)>48*1024*1024: raise ValueError('input_too_large')
    req=json.loads(data)
    if req.get('snapshot'): unpack(req['snapshot'])
    child=subprocess.Popen(req['argv'],cwd='/workspace',stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,
                           env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/workspace','LANG':'C.UTF-8'})
    selector=selectors.DefaultSelector()
    selector.register(child.stdout,selectors.EVENT_READ,'stdout'); selector.register(child.stderr,selectors.EVENT_READ,'stderr')
    buffers={'stdout':bytearray(),'stderr':bytearray()}; total=0; saved=0
    deadline=time.monotonic()+min(float(req['timeout']),120); timed_out=False; killed=False
    while selector.get_map():
        now=time.monotonic()
        if now>=deadline and not timed_out:
            timed_out=True
            try: os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError: pass
        if now>=deadline+3 and not killed:
            killed=True
            try: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        # Detached grandchildren are contained by Docker and killed when PID 1 exits.
        if now>=deadline+4: break
        for key,_ in selector.select(.05):
            chunk=os.read(key.fileobj.fileno(),65536)
            if not chunk:
                selector.unregister(key.fileobj); continue
            total+=len(chunk); keep=chunk[:max(0,OUTPUT_LIMIT-saved)]
            buffers[key.data].extend(keep); saved+=len(keep)
    if child.poll() is None:
        try: os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError: pass
    code=child.wait(timeout=1)
    # Stop descendants even after a successful parent exit, before building a snapshot.
    try: os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    result={'status':'timed_out' if timed_out else 'succeeded' if code==0 else 'failed',
            'output':buffers['stdout'].decode(errors='replace'),'stderr':buffers['stderr'].decode(errors='replace'),
            'output_bytes':total,'truncated':total>saved,'exit_code':code if code>=0 else None,
            'term_signal':-code if code<0 else None,'error_code':'tool_timeout' if timed_out else None}
    if result['status']=='succeeded':
        try: result['snapshot']=pack()
        except ValueError as exc: result.update(status='failed',error_code=str(exc))
    print(json.dumps(result,ensure_ascii=True),flush=True)

if __name__=='__main__': main()
