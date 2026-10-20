"""SQL authoritative memory with versioned retraction and fenced background writes."""
import json
import time
from sqlalchemy import select, insert, update, or_
from hosting import schema as hs
from hosting.repository import Conflict, LostLease, uid
from . import durable_schema as s
from .context_service import bounded

class MemoryRepository:
    def __init__(self, repo):
        self.repo = repo

    def _scope(self, c, scope):
        session = self.repo._session(c, scope.user_id, scope.session_id)
        if session['workspace_id'] != scope.workspace_id:
            raise Conflict('not_found', 404)

    def read(self, scope, watermark=None):
        with self.repo.engine.connect() as c:
            self._scope(c, scope)
            where = [s.entries.c.user_id==scope.user_id, s.entries.c.workspace_id==scope.workspace_id,
                     s.entries.c.session_id==scope.session_id, s.entries.c.status=='active']
            if watermark is not None:
                where.append(s.entries.c.watermark <= watermark)
            rows = [dict(r) for r in c.execute(select(s.entries).where(*where)).mappings()]
            prefs = c.execute(select(s.preferences).where(s.preferences.c.user_id==scope.user_id,
                s.preferences.c.workspace_id==scope.workspace_id, s.preferences.c.status=='active').order_by(s.preferences.c.field)).mappings()
            items = [dict(r, kind='preference', content=r['field']+': '+r['value_json']) for r in prefs]
            items.extend(rows)
            return {'items':items, 'watermark':max([r['watermark'] for r in rows],default=0)}

    def episodes(self,scope,query):
        from marketing.feedback.procedures import words
        with self.repo.engine.connect() as c:
            self._scope(c,scope)
            rows=c.execute(select(s.entries).where(s.entries.c.user_id==scope.user_id,s.entries.c.workspace_id==scope.workspace_id,
                s.entries.c.session_id!=scope.session_id,s.entries.c.kind=='summary',s.entries.c.status=='active').order_by(s.entries.c.updated_at.desc()).limit(50)).mappings()
            ranked=[(len(words(query)&words(r['content'])),dict(r,kind='episode',content='历史对话摘要，非效果证据：'+r['content'])) for r in rows]
            return [r for score,r in sorted(ranked,key=lambda pair:(-pair[0],str(pair[1]['id']))) if score>0][:3]

    def history_cutoff(self,scope):
        from .extended_schema import suppressed
        from sqlalchemy import func
        with self.repo.engine.connect() as c:
            self._scope(c,scope)
            return c.execute(select(func.max(suppressed.c.created_at)).where(suppressed.c.user_id==scope.user_id,
                suppressed.c.session_id==scope.session_id)).scalar() or 0


    def list_preferences(self, scope):
        with self.repo.engine.connect() as c:
            self._scope(c, scope)
            return [dict(r) for r in c.execute(select(s.preferences).where(s.preferences.c.user_id==scope.user_id,
                s.preferences.c.workspace_id==scope.workspace_id)).mappings()]

    def preference(self, scope, field, values, expected_version, source_ref=None, source_order=None, automatic=False):
        from .conversation_memory import PROFILE_FIELDS, sanitize_user_profile
        if field not in PROFILE_FIELDS:
            raise Conflict('invalid_preference_field', 400)
        clean = sanitize_user_profile({field:values}).get(field, [])
        if values and not clean:
            raise Conflict('invalid_preference_value', 400)
        with self.repo.tx() as c:
            self._scope(c, scope)
            return self._preference(c, scope, field, clean, expected_version, source_ref, source_order, automatic)

    def _preference(self, c, scope, field, values, expected_version, source_ref, source_order, automatic):
        row = c.execute(select(s.preferences).where(s.preferences.c.user_id==scope.user_id,
            s.preferences.c.workspace_id==scope.workspace_id, s.preferences.c.field==field).with_for_update()).mappings().first()
        version = row['version'] if row else 0
        if expected_version != version:
            raise Conflict('memory_version_conflict')
        order = time.time() if source_order is None else source_order
        if automatic and row and order <= row['source_order']:
            return dict(row)
        if row:
            self._revision(c,scope,'preference',dict(row))
        # Automatic changes never silently override a conflicting explicit preference or tombstone.
        status = 'active' if values else 'revoked'
        encoded = json.dumps(values, ensure_ascii=False)
        if automatic and row and (row['status']=='revoked' or row['value_json']!=encoded):
            from .extended_schema import candidates
            if source_ref:
                existing=c.execute(select(candidates.c.id).where(candidates.c.user_id==scope.user_id,
                    candidates.c.workspace_id==scope.workspace_id,candidates.c.field==field,candidates.c.source_ref==source_ref)).first()
                if not existing:
                    c.execute(insert(candidates).values(id=uid(),user_id=scope.user_id,workspace_id=scope.workspace_id,
                        session_id=scope.session_id,field=field,value_json=encoded,source_ref=source_ref,
                        source_order=order,expected_version=version,status='pending_confirmation',created_at=time.time()))
            return dict(row)
        ident = row['id'] if row else uid()
        data = dict(user_id=scope.user_id, workspace_id=scope.workspace_id, field=field,
            value_json=encoded, version=version+1, status=status, source_ref=source_ref,
            source_order=order, updated_at=time.time())
        if row:
            c.execute(update(s.preferences).where(s.preferences.c.id==ident).values(**data))
        else:
            c.execute(insert(s.preferences).values(id=ident, **data))
        c.execute(insert(s.events).values(id=uid(), preference_id=ident, version=version+1,
            action='revoke' if not values else 'set', created_at=time.time()))
        self._revision(c,scope,'preference',dict(id=ident,**data))
        return dict(id=ident, **data)

    def clear_session(self, scope):
        with self.repo.tx() as c:
            self._scope(c, scope)
            row = c.execute(select(s.entries).where(s.entries.c.user_id==scope.user_id,
                s.entries.c.session_id==scope.session_id, s.entries.c.kind=='summary')).mappings().first()
            watermark = c.execute(select(hs.sessions.c.next_seq).where(hs.sessions.c.id==scope.session_id)).scalar_one()
            data = dict(content='', source_ref=None, watermark=watermark, status='revoked',
                        version=(row['version'] if row else 0)+1, updated_at=time.time())
            if row:
                c.execute(update(s.entries).where(s.entries.c.id==row['id']).values(**data))
            else:
                c.execute(insert(s.entries).values(id=uid(), user_id=scope.user_id,workspace_id=scope.workspace_id,
                    session_id=scope.session_id,kind='summary',**data))
        return {'ok':True}

    def claim(self):
        with self.repo.tx() as c:
            now = time.time()
            row = c.execute(select(s.jobs).where(or_(s.jobs.c.status.in_(['pending','retry']),
                (s.jobs.c.status=='running') & (s.jobs.c.lease_until < now)),s.jobs.c.next_attempt_at<=now,
                s.jobs.c.attempts<5).order_by(s.jobs.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if not row:
                c.execute(update(s.jobs).where(s.jobs.c.attempts>=5,s.jobs.c.status=='running',s.jobs.c.lease_until<now).values(status='dead'))
                return None
            job = dict(row, lease_token=row['lease_token']+1, attempts=row['attempts']+1, status='running',lease_until=now+60)
            c.execute(update(s.jobs).where(s.jobs.c.id==row['id']).values(**{k:job[k] for k in ('lease_token','attempts','status','lease_until')}))
            return job

    def source(self, job):
        with self.repo.engine.connect() as c:
            run = self.repo._row(c, hs.runs, job['run_id'])
            if run['status']!='succeeded':
                raise Conflict('memory_source_not_succeeded')
            rows = list(c.execute(select(hs.messages).where(hs.messages.c.run_id==run['id'],hs.messages.c.is_partial.is_(False)).order_by(hs.messages.c.seq)).mappings())
        # Never use content_preview as the authoritative extraction input.
        from hosting.objects import message_text
        messages = [dict(r, content=message_text(self.repo.objects, r)) for r in rows]
        return run, messages

    def complete(self, job, summary, candidates):
        from .context_service import Scope, enabled, allowed_subject
        from .conversation_memory import PROFILE_FIELDS, sanitize_user_profile
        if not enabled('AGENT_MEMORY_WRITE_ENABLED'):
            return
        run, messages = self.source(job)
        scope = Scope(run['user_id'], run['workspace_id'], run['session_id'])
        user = next((m for m in messages if m['role']=='user'), None)
        watermark = max((m['seq'] for m in messages), default=0)
        with self.repo.tx() as c:
            current = self.repo._row(c,s.jobs,job['id'])
            if current['status']!='running' or current['lease_token']!=job['lease_token'] or current['lease_until']<=time.time():
                raise LostLease('memory_lease_lost')
            self._scope(c,scope)
            if not allowed_subject(self.repo._row(c,hs.users,scope.user_id)['auth_subject']):
                return
            row = c.execute(select(s.entries).where(s.entries.c.user_id==scope.user_id,
                s.entries.c.session_id==scope.session_id,s.entries.c.kind=='summary')).mappings().first()
            from .extended_schema import suppressed
            if not user or c.execute(select(suppressed.c.message_id).where(suppressed.c.user_id==scope.user_id,
                suppressed.c.session_id==scope.session_id,suppressed.c.created_at>=run['created_at'])).first():
                c.execute(update(s.jobs).where(s.jobs.c.id==job['id']).values(status='succeeded',lease_until=None,error_code='source_erased'))
                return
            # Revocation stops all automatic re-creation for this session.
            if summary and (not row or (row['status']=='active' and row['watermark']<watermark)):
                content = bounded(summary,800)
                data = dict(content=content, source_ref=('message:'+str(user['id'])) if user else None, watermark=watermark,
                    version=(row['version'] if row else 0)+1,status='active',updated_at=time.time())
                if row:
                    self._revision(c,scope,'summary',dict(row))
                    c.execute(update(s.entries).where(s.entries.c.id==row['id']).values(**data))
                else:
                    c.execute(insert(s.entries).values(id=uid(),user_id=scope.user_id,workspace_id=scope.workspace_id,
                        session_id=scope.session_id,kind='summary',**data))
                summary_row=c.execute(select(s.entries).where(s.entries.c.user_id==scope.user_id,s.entries.c.session_id==scope.session_id,s.entries.c.kind=='summary')).mappings().one()
                self._revision(c,scope,'summary',dict(summary_row))
            for candidate in candidates[:8]:
                if not isinstance(candidate,dict):
                    continue
                field, quote, values = candidate.get('field'),candidate.get('quote'),candidate.get('values')
                if not user or field not in PROFILE_FIELDS or not isinstance(quote,str) or not quote or quote not in user['content']:
                    continue
                # Only literal user values with an explicit long-term marker qualify automatically.
                if not any(marker in quote for marker in ('长期','以后都','始终','一直')) or any(marker in user['content'] for marker in ('只这次','只这一次','仅本次')):
                    continue
                clean = sanitize_user_profile({field:values}).get(field,[])
                if not clean or any(v not in quote for v in clean):
                    continue
                old = c.execute(select(s.preferences).where(s.preferences.c.user_id==scope.user_id,
                    s.preferences.c.workspace_id==scope.workspace_id,s.preferences.c.field==field)).mappings().first()
                self._preference(c,scope,field,clean,old['version'] if old else 0,'message:'+str(user['id']),run['created_at'],True)
            c.execute(update(s.jobs).where(s.jobs.c.id==job['id']).values(status='succeeded',lease_until=None,error_code=None))

    def fail(self, job, code):
        with self.repo.tx() as c:
            c.execute(update(s.jobs).where(s.jobs.c.id==job['id'],s.jobs.c.lease_token==job['lease_token'],s.jobs.c.status=='running').values(
                status='dead' if job['attempts']>=5 else 'retry',lease_until=None,
                next_attempt_at=time.time()+min(300,2**job['attempts']),error_code=code[:80]))

    def revisions(self, scope, kind, ident):
        from .extended_schema import revisions
        with self.repo.engine.connect() as c:
            self._scope(c,scope)
            return [dict(r) for r in c.execute(select(revisions).where(revisions.c.user_id==scope.user_id,
                revisions.c.workspace_id==scope.workspace_id,revisions.c.entity_kind==kind,
                revisions.c.entity_id==ident).order_by(revisions.c.version)).mappings()]

    def _revision(self,c,scope,kind,row):
        from .extended_schema import revisions
        exists=c.execute(select(revisions.c.id).where(revisions.c.entity_kind==kind,
            revisions.c.entity_id==row['id'],revisions.c.version==row['version'])).first()
        if not exists:
            c.execute(insert(revisions).values(id=uid(),user_id=scope.user_id,workspace_id=scope.workspace_id,
                entity_kind=kind,entity_id=row['id'],version=row['version'],
                payload=json.dumps(dict(row),ensure_ascii=False),source_ref=row.get('source_ref'),
                status=row['status'],created_at=time.time()))

    def candidates(self, scope):
        from .extended_schema import candidates
        with self.repo.engine.connect() as c:
            self._scope(c,scope)
            return [dict(r) for r in c.execute(select(candidates).where(candidates.c.user_id==scope.user_id,
                candidates.c.workspace_id==scope.workspace_id,candidates.c.status=='pending_confirmation')).mappings()]

    def resolve_candidate(self,scope,ident,accept,expected_version):
        from .extended_schema import candidates
        with self.repo.tx() as c:
            self._scope(c,scope)
            row=c.execute(select(candidates).where(candidates.c.id==ident,candidates.c.user_id==scope.user_id,
                candidates.c.workspace_id==scope.workspace_id).with_for_update()).mappings().first()
            if not row: raise Conflict('not_found',404)
            if row['status']!='pending_confirmation': raise Conflict('candidate_already_resolved')
            if accept:
                value=self._preference(c,scope,row['field'],json.loads(row['value_json']),expected_version,row['source_ref'],None,False)
            else: value={'status':'rejected'}
            c.execute(update(candidates).where(candidates.c.id==ident).values(status='accepted' if accept else 'rejected'))
            return value

    def list_jobs(self,scope):
        with self.repo.engine.connect() as c:
            self._scope(c,scope)
            return [dict(r) for r in c.execute(select(s.jobs).join(hs.runs,hs.runs.c.id==s.jobs.c.run_id).where(
                hs.runs.c.user_id==scope.user_id,hs.runs.c.session_id==scope.session_id).order_by(s.jobs.c.created_at.desc()).limit(100)).mappings()]

    def replay_job(self,scope,ident):
        with self.repo.tx() as c:
            self._scope(c,scope)
            row=c.execute(select(s.jobs).join(hs.runs,hs.runs.c.id==s.jobs.c.run_id).where(s.jobs.c.id==ident,
                hs.runs.c.user_id==scope.user_id,hs.runs.c.session_id==scope.session_id).with_for_update()).mappings().first()
            if not row: raise Conflict('not_found',404)
            if row['status'] not in ('dead','retry'): raise Conflict('job_not_replayable')
            c.execute(update(s.jobs).where(s.jobs.c.id==ident).values(status='pending',attempts=0,
                lease_token=row['lease_token']+1,lease_until=None,next_attempt_at=time.time(),error_code=None))
        return {'ok':True}

    def suppress_source(self,scope,message_id):
        """Erase user-visible source, invalidate mixed summaries, fence queued extraction."""
        from .extended_schema import suppressed, revisions, candidates
        with self.repo.tx() as c:
            self._scope(c,scope)
            message=c.execute(select(hs.messages).where(hs.messages.c.id==message_id,hs.messages.c.user_id==scope.user_id,
                hs.messages.c.session_id==scope.session_id).with_for_update()).mappings().first()
            if not message: raise Conflict('not_found',404)
            if not c.execute(select(suppressed.c.message_id).where(suppressed.c.message_id==message_id)).first():
                c.execute(insert(suppressed).values(message_id=message_id,user_id=scope.user_id,workspace_id=scope.workspace_id,
                    session_id=scope.session_id,created_at=time.time()))
            c.execute(update(hs.messages).where(hs.messages.c.id==message_id).values(content_preview='',content_ref=None,content_bytes=0,is_partial=True))
            c.execute(update(hs.sessions).where(hs.sessions.c.id==scope.session_id).values(version=hs.sessions.c.version+1))
            c.execute(update(s.entries).where(s.entries.c.user_id==scope.user_id,s.entries.c.session_id==scope.session_id).values(
                content='',source_ref=None,status='revoked',version=s.entries.c.version+1))
            ref='message:'+str(message_id)
            c.execute(update(s.preferences).where(s.preferences.c.user_id==scope.user_id,s.preferences.c.source_ref==ref).values(
                value_json='[]',source_ref=None,status='revoked',version=s.preferences.c.version+1))
            c.execute(update(candidates).where(candidates.c.user_id==scope.user_id,candidates.c.source_ref==ref).values(status='revoked',value_json='[]'))
            c.execute(update(revisions).where(revisions.c.user_id==scope.user_id,
                (revisions.c.source_ref==ref) | ((revisions.c.entity_kind=='summary') & revisions.c.entity_id.in_(
                    select(s.entries.c.id).where(s.entries.c.user_id==scope.user_id,s.entries.c.session_id==scope.session_id)))).values(payload='{}',status='revoked'))
            active=list(c.execute(select(hs.runs).where(hs.runs.c.user_id==scope.user_id,hs.runs.c.session_id==scope.session_id,
                hs.runs.c.status.in_(['queued','running','waiting_approval']))).mappings())
            for run in active:
                if run['status']=='running':
                    c.execute(update(hs.runs).where(hs.runs.c.id==run['id']).values(status='stopping',stop_reason='cancelled'))
                else:
                    self.repo._finish(c,dict(run),'cancelled','memory_source_erased')
            run_ids=select(hs.runs.c.id).where(hs.runs.c.user_id==scope.user_id,hs.runs.c.session_id==scope.session_id)
            c.execute(update(s.jobs).where(s.jobs.c.run_id.in_(run_ids),s.jobs.c.status!='succeeded').values(
                status='succeeded',lease_token=s.jobs.c.lease_token+1,lease_until=None,error_code='source_erased'))
        return {'ok':True}
