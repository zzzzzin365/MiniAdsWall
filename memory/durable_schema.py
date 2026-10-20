"""Memory v1 schema, migrated independently of the immutable initial schema."""
from sqlalchemy import MetaData, Table, Column as C, BigInteger, Integer, String, Text, Float, UniqueConstraint, Index
metadata = MetaData()
ID = BigInteger().with_variant(Integer, 'sqlite')
def table(name, *cols):
    return Table(name, metadata, *cols, mysql_engine='InnoDB', mysql_charset='utf8mb4')
entries = table('agent_memory_entries', C('id', ID, primary_key=True), C('user_id', ID, nullable=False),
    C('workspace_id', ID, nullable=False), C('session_id', ID, nullable=False), C('kind', String(24), nullable=False),
    C('content', Text, nullable=False), C('source_ref', String(64)), C('watermark', ID, nullable=False),
    C('version', Integer, nullable=False), C('status', String(20), nullable=False), C('updated_at', Float, nullable=False),
    UniqueConstraint('user_id','workspace_id','session_id','kind',name='uq_agent_memory_entry'))
preferences = table('agent_memory_preferences', C('id', ID, primary_key=True), C('user_id', ID, nullable=False),
    C('workspace_id', ID, nullable=False), C('field', String(80), nullable=False), C('value_json', Text, nullable=False),
    C('version', Integer, nullable=False), C('status', String(24), nullable=False), C('source_ref', String(64)),
    C('source_order', Float, nullable=False), C('updated_at', Float, nullable=False),
    UniqueConstraint('user_id','workspace_id','field',name='uq_agent_memory_preference'))
events = table('agent_memory_preference_events', C('id', ID, primary_key=True), C('preference_id', ID, nullable=False),
    C('version', Integer, nullable=False), C('action', String(24), nullable=False), C('created_at', Float, nullable=False),
    UniqueConstraint('preference_id','version',name='uq_agent_memory_preference_event'))
jobs = table('agent_memory_jobs', C('id', ID, primary_key=True), C('run_id', ID, nullable=False),
    C('status', String(20), nullable=False), C('attempts', Integer, nullable=False), C('lease_token', Integer, nullable=False),
    C('lease_until', Float), C('next_attempt_at', Float, nullable=False), C('error_code', String(80)),
    C('created_at', Float, nullable=False), UniqueConstraint('run_id',name='uq_agent_memory_job_run'),
    Index('ix_agent_memory_job_pending','status','next_attempt_at'))
