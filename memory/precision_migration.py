"""v5: MySQL FLOAT cannot represent epoch timestamps at lease precision.
Keep v3 immutable; widen existing fields with resumable additive ALTERs.
"""
from sqlalchemy import inspect, Double
COLUMNS={
 'agent_memory_entries': [('updated_at',False)],
 'agent_memory_preferences': [('source_order',False),('updated_at',False)],
 'agent_memory_preference_events': [('created_at',False)],
 'agent_memory_jobs': [('lease_until',True),('next_attempt_at',False),('created_at',False)],
}
def apply(c,after_statement):
    if c.dialect.name!='mysql':
        after_statement(5,0)
        return
    index=0
    for table,columns in COLUMNS.items():
        for name,nullable in columns:
            actual=next(v for v in inspect(c).get_columns(table) if v['name']==name)
            if not isinstance(actual['type'],Double):
                c.exec_driver_sql('ALTER TABLE '+table+' MODIFY '+name+' DOUBLE '+('NULL' if nullable else 'NOT NULL'))
                c.commit()
            after_statement(5,index);index+=1

def verify(c):
    if c.dialect.name!='mysql':return
    from hosting.migrations import MigrationError
    for table,columns in COLUMNS.items():
        actual={v['name']:v for v in inspect(c).get_columns(table)}
        for name,nullable in columns:
            if not isinstance(actual[name]['type'],Double) or actual[name]['nullable']!=nullable:
                raise MigrationError('memory_timestamp_precision_required')
