"""Authoritative reviewed procedures; hosted workspaces are the authorization boundary.
No brand-specific outcomes are accepted by this generic-procedure store.
"""
from sqlalchemy import MetaData, Table, Column as C, BigInteger, Integer, String, Text, Double as Float, Index
metadata=MetaData()
ID=BigInteger().with_variant(Integer,'sqlite')
cases=Table('marketing_reviewed_procedures',metadata,
    C('id',ID,primary_key=True),C('user_id',ID,nullable=False),C('workspace_id',ID,nullable=False),
    C('title',String(120),nullable=False),C('objective',String(120),nullable=False),
    C('conditions_json',Text,nullable=False),C('steps_json',Text,nullable=False),
    C('limitations_json',Text,nullable=False),C('source_title',String(160),nullable=False),
    C('version',Integer,nullable=False),C('status',String(24),nullable=False),
    C('reviewed_by',String(190)),C('expires_at',Float),C('created_at',Float,nullable=False),C('updated_at',Float,nullable=False),
    Index('ix_reviewed_procedure_scope','user_id','workspace_id','status','updated_at'),
    mysql_engine='InnoDB',mysql_charset='utf8mb4')
