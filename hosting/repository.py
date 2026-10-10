"""Short transactions. Global admission lock serializes lifecycle writes, never queries/model work.
All times are UTC Unix seconds (Float); durations use monotonic clocks outside SQL.
"""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import secrets
import time
from sqlalchemy import create_engine, select, update, insert, delete, func, and_, or_
from . import schema as s
from .reliability import load_reliability
from .objects import preview

TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted'}
ACTIVE = ('running', 'stopping')
WAITING = ('queued', 'waiting_approval')
class Conflict(Exception):
    def __init__(self, message, status=409): self.status = status; super().__init__(message)
class LostLease(Conflict): pass

def uid(): return secrets.randbits(62) + 1
def dumps(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
def public(value):
    if isinstance(value, dict):
        return {k: (str(v) if v is not None and (k == 'id' or k.endswith('_id')) else public(v)) for k,v in value.items()}
    if isinstance(value, list): return [public(v) for v in value]
    return value

class Repository:
    def __init__(self, url, objects, cursor_secret, *, test=False, max_running=16, user_running=2,
                 max_waiting=1000, user_waiting=10, run_seconds=600, queue_seconds=300):
        if not test and not url.startswith('mysql+pymysql://'):
            raise ValueError('Production requires mysql+pymysql DATABASE_URL')
        self.test = test
        options = {'pool_pre_ping': True}
        if url == 'sqlite://':
            # A shared single connection can let a concurrent reader roll back another
            # thread's transaction. Use isolated file-backed connections even in tests.
            import tempfile
            self._test_db_dir=tempfile.TemporaryDirectory(prefix='agent-repo-')
            url='sqlite:///'+self._test_db_dir.name+'/test.sqlite3'
            options.update(connect_args={'check_same_thread':False})
        elif url.startswith('mysql'): options.update(pool_size=5, max_overflow=0, pool_timeout=3,
                                                   connect_args={'connect_timeout':3,'read_timeout':5,'write_timeout':5})
        self.engine = create_engine(url, **options)
        import threading
        self.test_lock = threading.RLock()
        self.objects, self.secret = objects, cursor_secret.encode()
        if len(self.secret) < 32: raise ValueError('CURSOR_SECRET must contain at least 32 bytes')
        self.max_running, self.user_running = max_running, user_running
        self.max_waiting, self.user_waiting = max_waiting, user_waiting
        self.run_seconds, self.queue_seconds = run_seconds, queue_seconds

    def migrate(self):
        if self.test and self.engine.dialect.name=='sqlite':
            with self.engine.connect() as c: c.exec_driver_sql('PRAGMA journal_mode=WAL')
        from .migrations import upgrade
        return upgrade(self.engine)

    @contextmanager
    def tx(self):
        # SQLite has no row locks: used ONLY for deterministic tests.
        with self.test_lock if self.test else _null():
            with self.engine.begin() as c:
                c.execute(select(s.capacity).where(s.capacity.c.scope=='global').with_for_update()).first()
                yield c

    def _row(self, c, table, ident):
        row = c.execute(select(table).where(table.c.id==int(ident))).mappings().first()
        if not row: raise Conflict('not_found',404)
        return dict(row)

    def _session(self,c,user,ident):
        row = self._row(c,s.sessions,ident)
        member = c.execute(select(s.members.c.role).where(s.members.c.workspace_id==row['workspace_id'],s.members.c.user_id==int(user))).scalar()
        if row['user_id']!=int(user) or not member: raise Conflict('not_found',404)
        account = self._row(c,s.users,user)
        if account['status']!='active': raise Conflict('account_disabled',403)
        return row

    def _run(self,c,user,ident):
        row=self._row(c,s.runs,ident); self._session(c,user,row['session_id']); return row

    def provision(self,subject):
        with self.tx() as c:
            row=c.execute(select(s.users).where(s.users.c.auth_subject==subject)).mappings().first()
            if row:
                if row['status']!='active': raise Conflict('account_disabled',403)
                user=row['id']
                workspace=c.execute(select(s.workspaces.c.id).where(s.workspaces.c.owner_id==user)).scalar_one()
            else:
                user,workspace=uid(),uid(); now=time.time()
                c.execute(insert(s.users).values(id=user,auth_subject=subject,status='active',created_at=now))
                c.execute(insert(s.workspaces).values(id=workspace,owner_id=user,name='My workspace',version=1,created_at=now))
                c.execute(insert(s.members).values(workspace_id=workspace,user_id=user,role='owner'))
                c.execute(insert(s.capacity).values(scope=f'user:{user}',last_dispatch_at=0))
            return {'user_id':user,'workspace_id':workspace}

    def create_session(self,user,workspace,title):
        with self.tx() as c:
            if not c.execute(select(s.members.c.role).where(s.members.c.workspace_id==int(workspace),s.members.c.user_id==int(user))).scalar():
                raise Conflict('not_found',404)
            if c.execute(select(func.count()).select_from(s.sessions).where(s.sessions.c.user_id==int(user))).scalar()>=100:
                raise Conflict('session_limit',429)
            ident,now=uid(),time.time()
            c.execute(insert(s.sessions).values(id=ident,user_id=int(user),workspace_id=int(workspace),title=title[:120],status='active',next_seq=0,version=1,updated_at=now,created_at=now))
            return self._row(c,s.sessions,ident)

    def workspace_snapshot(self,user,wid,archive):
        import io, tarfile
        if len(archive)>32*1024*1024: raise Conflict('snapshot_too_large',413)
        try:
            total=0
            with tarfile.open(fileobj=io.BytesIO(archive),mode='r:') as tar:
                for i,member in enumerate(tar):
                    from pathlib import PurePosixPath
                    if i>=10000 or member.name.startswith('/') or '..' in PurePosixPath(member.name).parts or not (member.isfile() or member.isdir()):
                        raise Conflict('unsafe_snapshot',400)
                    total+=member.size
                    if total>32*1024*1024: raise Conflict('snapshot_too_large',413)
        except tarfile.TarError as exc: raise Conflict('invalid_tar',400) from exc
        with self.tx() as c:
            workspace=self._row(c,s.workspaces,wid)
            if workspace['owner_id']!=int(user): raise Conflict('not_found',404)
            ref=self.objects.put(archive)
            c.execute(update(s.workspaces).where(s.workspaces.c.id==int(wid)).values(snapshot_ref=ref,version=workspace['version']+1))
            return {'workspace_id':int(wid),'version':workspace['version']+1,'snapshot_ref':ref}

    def commit_workspace(self,user,rid):
        with self.tx() as c:
            r=self._run(c,user,rid); workspace=self._row(c,s.workspaces,r['workspace_id'])
            if workspace['owner_id']!=int(user): raise Conflict('workspace_write_forbidden',403)
            if r['status']!='succeeded' or not r['checkpoint_ref']: raise Conflict('no_successful_workspace')
            checkpoint=json.loads(self.objects.get(r['checkpoint_ref'])); ref=checkpoint.get('workspace_ref')
            if not ref: raise Conflict('no_workspace_snapshot')
            if workspace['snapshot_ref']==ref: return {'version':workspace['version'],'workspace_id':workspace['id']}
            if workspace['version']!=r['workspace_version']: raise Conflict('workspace_changed')
            self.objects.get(ref)  # validate object existence/checksum before publishing
            c.execute(update(s.workspaces).where(s.workspaces.c.id==workspace['id']).values(snapshot_ref=ref,version=workspace['version']+1))
            self._audit(c,r,'workspace.commit','succeeded')
            return {'version':workspace['version']+1,'workspace_id':workspace['id']}

    def _count(self,c,states,user=None):
        query=select(func.count()).select_from(s.runs).where(s.runs.c.status.in_(states))
        if user is not None: query=query.where(s.runs.c.user_id==user)
        return c.execute(query).scalar()

    def _waiting_slot(self,c,user):
        if self._count(c,WAITING,user)>=self.user_waiting: raise Conflict('user_queue_full',429)
        if self._count(c,WAITING)>=self.max_waiting: raise Conflict('queue_full',503)

    def _audit(self,c,r,action,result):
        c.execute(insert(s.audit).values(id=uid(),workspace_id=r['workspace_id'],actor_id=r['user_id'],session_id=r['session_id'],run_id=r['id'],action=action,result=result,created_at=time.time()))

    def _event(self,c,r,kind,payload):
        seq=c.execute(select(s.runs.c.event_seq).where(s.runs.c.id==r['id'])).scalar()+1
        if seq>10000 and kind!='run.finished': raise Conflict('event_limit_exceeded')
        encoded=dumps(public(payload))
        if len(encoded.encode())>8192: raise Conflict('event_payload_too_large')
        c.execute(insert(s.events).values(run_id=r['id'],event_seq=seq,type=kind,payload_preview=encoded,created_at=time.time()))
        c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(event_seq=seq))

    def _message(self,c,r,role,text,partial=False):
        session=self._row(c,s.sessions,r['session_id']); seq=session['next_seq']+1
        ident=uid(); ref=self.objects.put(text) if len(text.encode())>8192 else None
        c.execute(insert(s.messages).values(id=ident,user_id=r['user_id'],session_id=r['session_id'],run_id=r['id'],seq=seq,role=role,content_preview=preview(text),content_ref=ref,content_bytes=len(text.encode()),is_partial=partial,created_at=time.time()))
        c.execute(insert(s.records).values(session_id=r['session_id'],seq=seq,record_type='message',record_id=ident))
        c.execute(update(s.sessions).where(s.sessions.c.id==r['session_id']).values(next_seq=seq,updated_at=time.time(),version=session['version']+1))

    def create_run(self,user,session_id,key,payload,resume=None):
        protocol=payload.get('input_protocol_version',1)
        if type(protocol) is not int or protocol!=1 or ((payload.get('ad_context') is not None or payload.get('conditions')) and not load_reliability().features['AD_CONTEXT_V1_ENABLED']):
            raise Conflict('unsupported_input_protocol',400)
        if not 16<=len(key)<=100: raise Conflict('invalid_idempotency_key',400)
        if len(str(payload.get('message','')).encode())>65536: raise Conflict('message_too_large',413)
        encoded=dumps(payload)
        if len(encoded.encode())>262144: raise Conflict('request_too_large',413)
        # ads is a server-owned snapshot; retrying the same user action keeps the FIRST snapshot.
        request_fields={k:v for k,v in payload.items() if k not in ('ads','ad_context','input_protocol_version') and not (k=='conditions' and not v)}
        if request_fields.get('ad_context') is None: request_fields.pop('ad_context',None)
        if not request_fields.get('conditions'): request_fields.pop('conditions',None)
        digest=hashlib.sha256(dumps({'payload':request_fields,'session_id':int(session_id),'resume':resume}).encode()).hexdigest()
        ref=self.objects.put(encoded)
        with self.tx() as c:
            session=self._session(c,user,session_id)
            old=c.execute(select(s.runs).where(s.runs.c.user_id==int(user),s.runs.c.idempotency_key==key)).mappings().first()
            if old:
                if old['request_hash']!=digest: raise Conflict('idempotency_key_reused')
                return dict(old)
            if session['active_run_id']: raise Conflict(f"session_busy:{session['active_run_id']}")
            self._waiting_slot(c,int(user))
            now=time.time(); workspace=self._row(c,s.workspaces,session['workspace_id'])
            checkpoint=self.objects.put(dumps({'workspace_ref':workspace['snapshot_ref']})) if workspace['snapshot_ref'] else None
            if resume:
                previous=self._run(c,user,resume)
                if previous['session_id']!=int(session_id) or previous['status'] not in TERMINAL-{'succeeded'}: raise Conflict('run_not_resumable')
                if previous['workspace_version']!=workspace['version']: raise Conflict('workspace_changed')
                # Never replay an uncertain shell/write step automatically.
                unsafe=c.execute(select(s.tools.c.id).where(s.tools.c.run_id==int(resume),s.tools.c.tool_name.in_(['shell','test']))).first()
                if unsafe: raise Conflict('shell_resume_requires_new_explicit_request')
                checkpoint=previous['checkpoint_ref']
            r=dict(id=uid(),user_id=int(user),workspace_id=session['workspace_id'],session_id=int(session_id),status='queued',idempotency_key=key,request_hash=digest,input_ref=ref,resumed_from_run_id=int(resume) if resume else None,fence_token=0,queue_deadline=now+self.queue_seconds,next_dispatch_at=now,workspace_version=workspace['version'],checkpoint_ref=checkpoint,event_seq=0,output_bytes=0,created_at=now)
            c.execute(insert(s.runs).values(**r)); c.execute(update(s.sessions).where(s.sessions.c.id==int(session_id)).values(active_run_id=r['id']))
            self._message(c,r,'user',payload['message']); self._audit(c,r,'run.created','queued'); self._event(c,r,'run.queued',{'run_id':r['id']})
            c.execute(insert(s.outbox).values(id=uid(),run_id=r['id'],attempts=0,next_attempt_at=now,created_at=now))
            return self._row(c,s.runs,r['id'])

    def claim(self,worker):
        with self.tx() as c:
            if self._count(c,ACTIVE)>=self.max_running: return None
            # Round-robin by last user dispatch; bounded by configured waiting capacity.
            candidates=c.execute(select(s.runs).join(s.capacity,s.capacity.c.scope==func.concat('user:',s.runs.c.user_id))
                .where(s.runs.c.status=='queued',s.runs.c.next_dispatch_at<=time.time(),func.coalesce(s.runs.c.input_protocol_version,1)==1)
                .order_by(s.capacity.c.last_dispatch_at,s.runs.c.created_at,s.runs.c.id).limit(self.max_waiting)).mappings() if not self.test else c.execute(select(s.runs).where(s.runs.c.status=='queued',func.coalesce(s.runs.c.input_protocol_version,1)==1).order_by(s.runs.c.created_at).limit(self.max_waiting)).mappings()
            for row in list(candidates):
                r=dict(row); now=time.time()
                if r['queue_deadline']<=now or (r['deadline_at'] and r['deadline_at']<=now):
                    self._finish(c,r,'timed_out','queue_timeout'); continue
                if self._count(c,ACTIVE,r['user_id'])>=self.user_running: continue
                self._session(c,r['user_id'],r['session_id'])
                c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(status='running',worker_id=worker,lease_until=now+20,fence_token=r['fence_token']+1,started_at=r['started_at'] or now,deadline_at=r['deadline_at'] or now+self.run_seconds))
                c.execute(update(s.capacity).where(s.capacity.c.scope==f"user:{r['user_id']}").values(last_dispatch_at=now))
                self._event(c,r,'run.started',{'run_id':r['id']}); self._audit(c,r,'run.started','running')
                return self._row(c,s.runs,r['id'])
        return None

    def guard(self,c,rid,token,stopping=False):
        r=self._row(c,s.runs,rid)
        if r['fence_token']!=token or r['status'] not in (ACTIVE if stopping else ('running',)) or not r['lease_until'] or r['lease_until']<=time.time():
            raise LostLease('run_lease_lost')
        if not stopping and r['deadline_at']<=time.time(): raise LostLease('run_deadline')
        return r

    def heartbeat(self,rid,token):
        with self.tx() as c:
            r=self.guard(c,rid,token,True)
            c.execute(update(s.runs).where(s.runs.c.id==int(rid)).values(lease_until=time.time()+20))
            return r

    def event(self,rid,token,kind,payload):
        with self.tx() as c:
            r=self.guard(c,rid,token); self._event(c,r,kind,payload)

    def checkpoint(self,rid,token,value):
        ref=self.objects.put(dumps(value))
        with self.tx() as c:
            self.guard(c,rid,token)
            c.execute(update(s.runs).where(s.runs.c.id==int(rid)).values(checkpoint_ref=ref))

    def get_run(self,user,rid):
        with self.engine.connect() as c: return self._run(c,user,rid)

    def _finish(self,c,r,status,error=None,text='',trace_ref=None):
        if r['status'] in TERMINAL: return r
        now=time.time()
        c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(status=status,error_code=error,finished_at=now,lease_until=None,trace_ref=trace_ref))
        c.execute(update(s.sessions).where(s.sessions.c.id==r['session_id'],s.sessions.c.active_run_id==r['id']).values(active_run_id=None,updated_at=now))
        # Every interrupted tool is explicitly finalized, even when its Worker died before recording output.
        c.execute(update(s.tools).where(s.tools.c.run_id==r['id'],s.tools.c.status=='running').values(status='interrupted',error_code=error or status,finished_at=now))
        c.execute(update(s.approvals).where(s.approvals.c.run_id==r['id'],s.approvals.c.status=='pending').values(status='expired'))
        if text:
            self._message(c,r,'assistant',text,status!='succeeded')
            if status=='succeeded':
                c.execute(insert(s.memories).values(id=uid(),user_id=r['user_id'],workspace_id=r['workspace_id'],session_id=r['session_id'],kind='last_reply',summary=preview(text),source_record_id=r['id'],version=1,created_at=now))
        self._audit(c,r,'run.finished',status)
        self._event(c,r,'run.finished',{'run_id':r['id'],'status':status,'error_code':error})
        return self._row(c,s.runs,r['id'])

    def finish(self,rid,token,status,text='',error=None,trace=None):
        trace_ref=self.objects.put(dumps(trace)) if trace else None
        with self.tx() as c:
            r=self.guard(c,rid,token,True)
            if r['status']=='stopping': status=r['stop_reason'] or 'cancelled'
            elif r['deadline_at']<=time.time(): status='timed_out'
            return self._finish(c,r,status,error,text,trace_ref)

    def stop(self,rid,token,reason):
        with self.tx() as c:
            r=self.guard(c,rid,token,True)
            if r['status']!='stopping': c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(status='stopping',stop_reason=reason))

    def cancel(self,user,rid):
        with self.tx() as c:
            r=self._run(c,user,rid)
            if r['status'] in TERMINAL: return r
            if r['status'] in WAITING: return self._finish(c,r,'cancelled')
            if r['status']!='stopping':
                c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(status='stopping',stop_reason='cancelled',cancel_requested_at=time.time()))
                self._audit(c,r,'run.cancel','requested')
            return self._row(c,s.runs,rid)

    def fence_expired(self,rid,token):
        """Revoke an expired Worker BEFORE asking Docker to remove its containers."""
        with self.tx() as c:
            r=self._row(c,s.runs,rid)
            if r['status'] not in ACTIVE or r['fence_token']!=token or r['lease_until']>=time.time(): return None
            token+=1
            c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(status='stopping',stop_reason=r['stop_reason'] or 'interrupted',fence_token=token))
            return token

    def expired(self):
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(s.runs).where(s.runs.c.status.in_(ACTIVE),s.runs.c.lease_until<time.time()).limit(100)).mappings()]

    def recover(self,rid,token):
        # Caller MUST confirm sandbox destruction first. Stale callbacks are fenced out.
        with self.tx() as c:
            r=self._row(c,s.runs,rid)
            if r['status'] not in ACTIVE or r['fence_token']!=token or r['lease_until']>=time.time(): return
            c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(fence_token=token+1))
            self._finish(c,r,r['stop_reason'] if r['status']=='stopping' and r['stop_reason'] else 'interrupted','worker_lost')

    def tool_start(self,rid,token,name,args,timeout,sandbox_id=None):
        encoded=dumps(args); ref=self.objects.put(encoded) if len(encoded.encode())>8192 else None
        with self.tx() as c:
            r=self.guard(c,rid,token); session=self._row(c,s.sessions,r['session_id']); ident,now=uid(),time.time(); seq=session['next_seq']+1
            c.execute(insert(s.tools).values(id=ident,user_id=r['user_id'],session_id=r['session_id'],run_id=r['id'],seq=seq,tool_name=name,args_preview=preview(encoded),args_ref=ref,status='running',deadline_at=min(r['deadline_at'],now+timeout),sandbox_id=sandbox_id,started_at=now,created_at=now))
            c.execute(insert(s.records).values(session_id=r['session_id'],seq=seq,record_type='tool_call',record_id=ident))
            c.execute(update(s.sessions).where(s.sessions.c.id==r['session_id']).values(next_seq=seq))
            self._event(c,r,'tool.started',{'tool_call_id':ident,'tool_name':name}); return ident

    def tool_finish(self,rid,token,tid,result):
        text=result.get('output',''); stderr=result.get('stderr',''); ref=None; unavailable=False
        stored=dumps({'stdout':text,'stderr':stderr})
        if len(text.encode())>8192 or len(stderr.encode())>8192:
            try: ref=self.objects.put(stored)
            except (OSError, ValueError): unavailable=True
        with self.tx() as c:
            r=self.guard(c,rid,token,True)
            total=r['output_bytes']+len(text.encode())+len(stderr.encode())
            if total>50*1024*1024: raise Conflict('run_output_limit')
            c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(output_bytes=total))
            values=dict(status=result.get('status','succeeded'),stdout_preview=preview(text),stderr_preview=preview(result.get('stderr','')),output_ref=ref,output_bytes=result.get('output_bytes',len(text.encode())),truncated=result.get('truncated',len(text.encode())>8192),cached=result.get('cached',False),exit_code=result.get('exit_code'),term_signal=result.get('term_signal'),error_code='output_unavailable' if unavailable else result.get('error_code'),finished_at=time.time())
            c.execute(update(s.tools).where(s.tools.c.id==int(tid),s.tools.c.run_id==int(rid),s.tools.c.status=='running').values(**values))
            if text: self._event(c,r,'tool.output',{'tool_call_id':tid,'text':preview(text,2048),'stream':'stdout'})
            self._event(c,r,'tool.finished',{'tool_call_id':tid,'status':values['status'],'exit_code':values['exit_code'],'truncated':values['truncated']})

    def wait_approval(self,rid,token,decision):
        with self.tx() as c:
            r=self.guard(c,rid,token); self._waiting_slot(c,r['user_id']); now=time.time(); ident=uid()
            c.execute(insert(s.approvals).values(id=ident,user_id=r['user_id'],workspace_id=r['workspace_id'],session_id=r['session_id'],run_id=r['id'],confirmation_id=decision['confirmation_id'],action_name=decision['action_name'],args_hash=decision['args_hash'],status='pending',expires_at=min(now+300,r['deadline_at']),created_at=now))
            c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(status='waiting_approval',lease_until=None))
            self._message(c,r,'assistant',decision['response'])
            self._event(c,r,'approval.required',{'approval_id':ident,'response':decision['response']})
            self._audit(c,r,'approval.requested','pending')

    def approve(self,user,aid,allow):
        with self.tx() as c:
            a=self._row(c,s.approvals,aid); r=self._run(c,user,a['run_id']); now=time.time()
            if a['status']!='pending': return r
            if r['status']!='waiting_approval': raise Conflict('approval_not_waiting')
            if a['expires_at']<=now or r['deadline_at']<=now:
                return self._finish(c,r,'timed_out','approval_expired')
            if not allow:
                c.execute(update(s.approvals).where(s.approvals.c.id==a['id']).values(status='rejected',decided_by=int(user),consumed_at=now))
                return self._finish(c,r,'cancelled','approval_rejected')
            # Snapshot integrity and membership are checked again when Worker resumes preflight.
            payload=json.loads(self.objects.get(r['input_ref']))
            if hashlib.sha256(dumps(payload).encode()).hexdigest()!=a['args_hash']: raise Conflict('approval_arguments_changed')
            c.execute(update(s.approvals).where(s.approvals.c.id==a['id']).values(status='approved',decided_by=int(user),consumed_at=now))
            c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(status='queued',next_dispatch_at=now,queue_deadline=min(now+self.queue_seconds,r['deadline_at'])))
            self._audit(c,r,'approval.decided','approved'); self._event(c,r,'run.queued',{'run_id':r['id']})
            return self._row(c,s.runs,r['id'])

    def approval_for(self,rid):
        with self.engine.connect() as c:
            row=c.execute(select(s.approvals).where(s.approvals.c.run_id==int(rid),s.approvals.c.status=='approved').order_by(s.approvals.c.created_at.desc()).limit(1)).mappings().first()
            return dict(row) if row else None

    def cursor(self,scope,value):
        raw=base64.urlsafe_b64encode(dumps([scope,value]).encode()).decode()
        return raw+'.'+hmac.new(self.secret,raw.encode(),hashlib.sha256).hexdigest()

    def uncursor(self,scope,cursor):
        if not cursor: return None
        try:
            raw,sig=cursor.split('.')
            if not hmac.compare_digest(sig,hmac.new(self.secret,raw.encode(),hashlib.sha256).hexdigest()): raise ValueError()
            actual,value=json.loads(base64.urlsafe_b64decode(raw))
            if actual!=scope: raise ValueError()
            return value
        except Exception: raise Conflict('invalid_cursor',400)

    def list_sessions(self,user,limit=20,cursor=None):
        limit=max(1,min(limit,100)); scope=f'sessions:{user}'; mark=self.uncursor(scope,cursor)
        query=select(s.sessions).join(s.members,and_(s.members.c.workspace_id==s.sessions.c.workspace_id,s.members.c.user_id==int(user))).where(s.sessions.c.user_id==int(user))
        if mark: query=query.where(or_(s.sessions.c.updated_at<mark[0],and_(s.sessions.c.updated_at==mark[0],s.sessions.c.id<mark[1])))
        with self.engine.connect() as c:
            rows=[dict(r) for r in c.execute(query.order_by(s.sessions.c.updated_at.desc(),s.sessions.c.id.desc()).limit(limit+1)).mappings()]
        more=len(rows)>limit; rows=rows[:limit]
        return {'items':rows,'next_cursor':self.cursor(scope,[rows[-1]['updated_at'],rows[-1]['id']]) if more else None}

    def history(self,user,sid,limit=50,cursor=None,kind=None):
        limit=max(1,min(limit,100)); scope=f'history:{user}:{sid}:{kind}'; mark=self.uncursor(scope,cursor)
        with self.engine.connect() as c:
            self._session(c,user,sid)
            table={'message':s.messages,'tool_call':s.tools}.get(kind,s.records)
            query=select(table).where(table.c.session_id==int(sid))
            if mark: query=query.where(table.c.seq<mark)
            rows=[dict(r) for r in c.execute(query.order_by(table.c.seq.desc()).limit(limit+1)).mappings()]
            more=len(rows)>limit; rows=rows[:limit]
            if not kind:
                mapping={}
                for typ,t in [('message',s.messages),('tool_call',s.tools)]:
                    ids=[r['record_id'] for r in rows if r['record_type']==typ]
                    if ids:
                        mapping.update({r['id']:dict(r) for r in c.execute(select(t).where(t.c.session_id==int(sid),t.c.id.in_(ids))).mappings()})
                rows=[dict(mapping[r['record_id']],record_type=r['record_type']) for r in rows]
            return {'items':rows,'next_cursor':self.cursor(scope,rows[-1]['seq']) if more else None}

    def read_events(self,user,rid,after,limit=32):
        with self.engine.connect() as c:
            r=self._run(c,user,rid)
            first=c.execute(select(func.min(s.events.c.event_seq)).where(s.events.c.run_id==int(rid))).scalar()
            if r['event_seq'] and (first is None or after<first-1): raise Conflict('events_expired',410)
            if after>r['event_seq']: raise Conflict('invalid_event_cursor',400)
            rows=[dict(e) for e in c.execute(select(s.events).where(s.events.c.run_id==int(rid),s.events.c.event_seq>after).order_by(s.events.c.event_seq).limit(limit)).mappings()]
            return r,rows

    def pending_op(self,key,op,raw=None,ttl=300):
        with self.tx() as c:
            row=c.execute(select(s.pending).where(s.pending.c.key==key)).mappings().first()
            if op=='put':
                if len(raw.encode())>65536: raise Conflict('pending_too_large')
                c.execute(delete(s.pending).where(s.pending.c.key==key))
                c.execute(insert(s.pending).values(key=key,payload=raw,expires_at=time.time()+ttl)); return
            if op=='pop': c.execute(delete(s.pending).where(s.pending.c.key==key))
            return row['payload'] if row and row['expires_at']>time.time() else None

@contextmanager
def _null(): yield
