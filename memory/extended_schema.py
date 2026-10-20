"""Additive memory lifecycle schema v4. Do not change the applied v3 definition."""
from sqlalchemy import MetaData, Table, Column as C, BigInteger, Integer, String, Text, Double as Float, UniqueConstraint, Index
metadata = MetaData()
ID = BigInteger().with_variant(Integer, 'sqlite')
def table(name, *columns):
    return Table(name, metadata, *columns, mysql_engine='InnoDB', mysql_charset='utf8mb4')
revisions = table('agent_memory_revisions', C('id', ID, primary_key=True),
    C('user_id', ID, nullable=False), C('workspace_id', ID, nullable=False),
    C('entity_kind', String(24), nullable=False), C('entity_id', ID, nullable=False),
    C('version', Integer, nullable=False), C('payload', Text, nullable=False),
    C('source_ref', String(100)), C('status', String(24), nullable=False), C('created_at', Float, nullable=False),
    UniqueConstraint('entity_kind','entity_id','version',name='uq_memory_revision'))
candidates = table('agent_memory_candidates', C('id', ID, primary_key=True),
    C('user_id', ID, nullable=False), C('workspace_id', ID, nullable=False), C('session_id', ID, nullable=False),
    C('field', String(80), nullable=False), C('value_json', Text, nullable=False), C('source_ref', String(100), nullable=False),
    C('source_order', Float, nullable=False), C('expected_version', Integer, nullable=False),
    C('status', String(24), nullable=False), C('created_at', Float, nullable=False),
    UniqueConstraint('user_id','workspace_id','field','source_ref',name='uq_memory_candidate'),
    Index('ix_memory_candidates_scope','user_id','workspace_id','status'))
suppressed = table('agent_memory_source_suppression', C('message_id', ID, primary_key=True),
    C('user_id', ID, nullable=False), C('workspace_id', ID, nullable=False), C('session_id', ID, nullable=False),
    C('created_at', Float, nullable=False))
