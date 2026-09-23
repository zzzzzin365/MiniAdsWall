"""Bounded maintenance of ephemeral records. Permanent history is never deleted here."""
import time
from sqlalchemy import select,delete
from . import schema as s

def cleanup(repo,batch=1000):
    now=time.time(); counts={}
    with repo.tx() as c:
        for name,table,key,condition in [
            ('pending',s.pending,s.pending.c.key,s.pending.c.expires_at<now),
            ('outbox',s.outbox,s.outbox.c.id,s.outbox.c.published_at<now-86400),
        ]:
            ids=list(c.execute(select(key).where(condition).limit(batch)).scalars())
            if ids: c.execute(delete(table).where(key.in_(ids)))
            counts[name]=len(ids)
        # Purge only terminal runs' replay data; active runs retain all events.
        rows=c.execute(select(s.events.c.run_id,s.events.c.event_seq).join(s.runs,s.runs.c.id==s.events.c.run_id)
            .where(s.runs.c.finished_at<now-7*86400,s.events.c.created_at<now-7*86400).limit(batch)).all()
        for rid,seq in rows:
            c.execute(delete(s.events).where(s.events.c.run_id==rid,s.events.c.event_seq==seq))
        counts['events']=len(rows)
    return counts

if __name__=='__main__':
    from dotenv import load_dotenv
    load_dotenv()
    from .config import repository
    repo=repository()
    try: print(cleanup(repo))
    finally: repo.engine.dispose()
