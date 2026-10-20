"""v6: persisted compatibility conversation aliases, scoped by authenticated subject."""
from sqlalchemy import MetaData,Table,Column,BigInteger,Integer,String,UniqueConstraint
metadata=MetaData()
ID=BigInteger().with_variant(Integer,'sqlite')
aliases=Table('agent_sync_conversations',metadata,Column('id',ID,primary_key=True),
    Column('user_id',ID,nullable=False),Column('alias_hash',String(64),nullable=False),
    Column('session_id',ID,nullable=False),UniqueConstraint('user_id','alias_hash',name='uq_sync_conversation_alias'),
    mysql_engine='InnoDB',mysql_charset='utf8mb4')
