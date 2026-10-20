"""Human-reviewed generic procedures, not verified performance outcomes."""
import json
import re
import time
from sqlalchemy import select, insert, update
from hosting.repository import Conflict, uid
from .schema import cases

def words(text):
    text=text.lower()
    return set(re.findall(r'[a-z0-9_]+',text)) | {text[i:i+2] for i in range(len(text)-1) if all('\u4e00'<=c<='\u9fff' for c in text[i:i+2])}

class ProcedureRepository:
    def __init__(self, repo): self.repo=repo
    def scope(self,c,scope):
        session=self.repo._session(c,scope.user_id,scope.session_id)
        if session['workspace_id']!=scope.workspace_id: raise Conflict('not_found',404)
    def review_permission(self,c,scope):
        from hosting.pipeline import permissions
        from hosting import schema as hs
        subject=self.repo._row(c,hs.users,scope.user_id)['auth_subject']
        if 'memory.review_procedure' not in permissions(subject): raise Conflict('procedure_review_required',403)
        return subject
    def list(self,scope):
        with self.repo.engine.connect() as c:
            self.scope(c,scope)
            return [dict(r) for r in c.execute(select(cases).where(cases.c.user_id==scope.user_id,cases.c.workspace_id==scope.workspace_id).order_by(cases.c.updated_at.desc()).limit(100)).mappings()]
    def save(self,scope,data,ident=None,expected_version=0,action='draft'):
        allowed={'title','objective','conditions','steps','limitations','source_title','expires_at'}
        if set(data)-allowed: raise Conflict('invalid_procedure_fields',400)
        if action not in {'draft','review','withdraw'}: raise Conflict('invalid_procedure_action',400)
        with self.repo.tx() as c:
            self.scope(c,scope)
            old=None
            if ident is not None:
                old=c.execute(select(cases).where(cases.c.id==ident,cases.c.user_id==scope.user_id,cases.c.workspace_id==scope.workspace_id).with_for_update()).mappings().first()
                if not old: raise Conflict('not_found',404)
            if (old['version'] if old else 0)!=expected_version: raise Conflict('procedure_version_conflict')
            reviewer=self.review_permission(c,scope) if action=='review' else None
            if action=='withdraw' and not old: raise Conflict('not_found',404)
            current=dict(old) if old else {}
            for field in ('title','objective','source_title'):
                value=data.get(field,current.get(field,''))
                if not isinstance(value,str) or not value.strip() or len(value)>120: raise Conflict('invalid_procedure_'+field,400)
                current[field]=value.strip()
            for field in ('conditions','steps','limitations'):
                value=data.get(field,json.loads(current.get(field+'_json','[]')))
                if not isinstance(value,list) or not 1<=len(value)<=8 or any(not isinstance(v,str) or not v.strip() or len(v)>240 for v in value): raise Conflict('invalid_procedure_'+field,400)
                current[field+'_json']=json.dumps(value,ensure_ascii=False)
            # This store only accepts generic workflows. Numeric performance claims need the full feedback ledger.
            content=' '.join([current['title'],current['objective'],current['steps_json']])
            from memory.conversation_memory import _PROMPT_INJECTION_PATTERNS
            if any(re.search(pattern,content,re.IGNORECASE) for pattern in _PROMPT_INJECTION_PATTERNS):
                raise Conflict('procedure_instruction_injection',400)
            if re.search(r'\d+(?:\.\d+)?\s*[%％]|收益保证|保证收益|已验证有效',content): raise Conflict('procedure_outcome_claim_not_allowed',400)
            expires=data.get('expires_at',current.get('expires_at'))
            if action!='withdraw' and expires is not None and (not isinstance(expires,(int,float)) or not time.time()<expires<time.time()+366*86400): raise Conflict('invalid_procedure_expiry',400)
            now=time.time();ident=old['id'] if old else uid()
            value={k:current[k] for k in ('title','objective','conditions_json','steps_json','limitations_json','source_title')}
            value.update(user_id=scope.user_id,workspace_id=scope.workspace_id,version=expected_version+1,
                status={'draft':'draft','review':'reviewed','withdraw':'withdrawn'}[action],reviewed_by=reviewer,
                expires_at=expires,updated_at=now)
            if old: c.execute(update(cases).where(cases.c.id==ident).values(**value))
            else: c.execute(insert(cases).values(id=ident,created_at=now,**value))
            return dict(id=ident,**value)
    def search(self,scope,query):
        q=words(query)
        matches=[]
        for row in self.list(scope):
            if row['status']!='reviewed' or (row['expires_at'] and row['expires_at']<=time.time()): continue
            conditions=json.loads(row['conditions_json'])
            # Conditions are hard query predicates, never inferred from a vector similarity score.
            if not all(term.lower() in query.lower() and not re.search(r'(?:不是|并非|不适用|排除|不要|非)\s*'+re.escape(term),query,re.IGNORECASE) for term in conditions): continue
            score=len(q & words(row['objective']+' '+row['title']))
            if not score: continue
            matches.append((score,dict(id=row['id'],version=row['version'],kind='reviewed_procedure',
                content=json.dumps({'title':row['title'],'conditions':conditions,'steps':json.loads(row['steps_json']),
                    'limitations':json.loads(row['limitations_json']),'evidence_level':'仅人工审核流程；没有效果或执行证明'},ensure_ascii=False),
                source_ref='procedure:'+str(row['id']),source_title=row['source_title'])))
        return [r for _,r in sorted(matches,key=lambda p:(-p[0],str(p[1]['id'])))[:3]]
