"""Explicit, serialized and resumable schema upgrades; no DDL in service startup."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
import uuid
from sqlalchemy import (MetaData, Table, Column, Integer, String, Double,
                        inspect, select, insert, update, text, UniqueConstraint)
from . import initial_schema

ROOT = Path(__file__).resolve().parents[1]
metadata = MetaData()
ledger = Table('hosting_schema_migrations', metadata,
    Column('version', Integer, primary_key=True), Column('name', String(100), nullable=False),
    Column('checksum', String(64), nullable=False), Column('state', String(16), nullable=False),
    Column('batch_id', String(36), nullable=False), Column('started_at', Double, nullable=False),
    Column('applied_at', Double), Column('error_code', String(100)), mysql_engine='InnoDB')
attempts = Table('hosting_migration_attempts', metadata,
    Column('version', Integer, primary_key=True), Column('batch_id', String(36), primary_key=True),
    Column('state', String(16), nullable=False), Column('started_at', Double, nullable=False),
    Column('finished_at', Double), Column('error_code', String(100)), mysql_engine='InnoDB')

class MigrationError(RuntimeError): pass

def check_column(c, table, name):
    cols = {v['name']: v for v in inspect(c).get_columns(table)}
    col = cols.get(name)
    if col is None: return False
    if not isinstance(col['type'], Integer) or not col['nullable'] or str(col['default']).strip("'\"") != '1':
        raise MigrationError('incompatible_column:' + table + '.' + name)
    return True

def verify_initial(c):
    inspector = inspect(c)
    for table in initial_schema.metadata.sorted_tables:
        if not inspector.has_table(table.name): raise MigrationError('missing_table:' + table.name)
        actual = {v['name'] for v in inspector.get_columns(table.name)}
        if not set(table.columns.keys()) <= actual: raise MigrationError('missing_columns:' + table.name)
        if set(inspector.get_pk_constraint(table.name)['constrained_columns']) != set(table.primary_key.columns.keys()):
            raise MigrationError('invalid_primary_key:' + table.name)
        indexes = {v['name'] for v in inspector.get_indexes(table.name)}
        if not {i.name for i in table.indexes} <= indexes: raise MigrationError('missing_indexes:' + table.name)
        actual_unique = {tuple(v['column_names']) for v in inspector.get_unique_constraints(table.name)}
        actual_unique.update(tuple(v['column_names']) for v in inspector.get_indexes(table.name) if v.get('unique'))
        expected_unique = {tuple(col.name for col in constraint.columns) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}
        if not expected_unique <= actual_unique: raise MigrationError('missing_unique_constraints:' + table.name)

def apply_initial(c, after_statement):
    initial_schema.metadata.create_all(c)
    c.commit()
    if c.execute(select(initial_schema.capacity.c.scope).where(initial_schema.capacity.c.scope == 'global')).first() is None:
        c.execute(insert(initial_schema.capacity).values(scope='global', last_dispatch_at=0))
    if c.execute(select(initial_schema.versions.c.version).where(initial_schema.versions.c.version == 1)).first() is None:
        c.execute(insert(initial_schema.versions).values(version=1, created_at=now(c)))
    c.commit()
    after_statement(1, 0)

PROTOCOL_COLUMNS = (('agent_runs', 'input_protocol_version'), ('tool_calls', 'executor_protocol_version'))

def apply_protocols(c, after_statement):
    statements = (ROOT / 'migrations/002_hosting_protocols.sql').read_text().split(';')
    for index, ((table, name), statement) in enumerate(zip(PROTOCOL_COLUMNS, statements)):
        if not check_column(c, table, name):
            c.exec_driver_sql(statement.strip())
            c.commit()  # MySQL DDL autocommits. Do not promise transactional rollback.
        after_statement(2, index)

def verify_protocols(c):
    for table, name in PROTOCOL_COLUMNS:
        if not check_column(c, table, name): raise MigrationError('missing_column:' + table + '.' + name)

@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    apply: object
    verify: object

def definitions():
    initial = (ROOT / 'migrations/001_hosting.sql').read_bytes() + (Path(__file__).parent / 'initial_schema.py').read_bytes()
    return [Migration(1, 'initial', hashlib.sha256(initial).hexdigest(), apply_initial, verify_initial),
            Migration(2, 'protocol_versions', hashlib.sha256((ROOT / 'migrations/002_hosting_protocols.sql').read_bytes()).hexdigest(), apply_protocols, verify_protocols)]

def now(c):
    return float(c.execute(text('SELECT UNIX_TIMESTAMP(NOW(6))')).scalar_one()) if c.dialect.name == 'mysql' else time.time()

@contextmanager
def migration_lock(engine, timeout):
    with engine.connect() as c:
        if c.dialect.name == 'mysql':
            database = c.execute(text('SELECT DATABASE()')).scalar_one()
            name = 'hosting:' + hashlib.sha256(database.encode()).hexdigest()[:50]
            if c.execute(text('SELECT GET_LOCK(:name,:timeout)'), dict(name=name, timeout=timeout)).scalar_one() != 1:
                raise MigrationError('migration_lock_timeout')
            c.commit()
            try: yield c
            finally:
                c.rollback()
                c.execute(text('SELECT RELEASE_LOCK(:name)'), dict(name=name))
                c.commit()
        elif c.dialect.name == 'sqlite':
            import fcntl
            path = engine.url.database
            if not path or path == ':memory:': raise MigrationError('file_backed_sqlite_required')
            with open(path + '.migration-lock', 'a') as lock:
                deadline = time.monotonic() + timeout
                while True:
                    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                    except BlockingIOError:
                        if time.monotonic() >= deadline: raise MigrationError('migration_lock_timeout')
                        time.sleep(.05)
                try: yield c
                finally: fcntl.flock(lock, fcntl.LOCK_UN)
        else: raise MigrationError('unsupported_migration_database')

def audit(c, migrations):
    known = {m.version: m for m in migrations}
    if inspect(c).has_table('schema_versions'):
        versions = c.execute(select(initial_schema.versions.c.version)).scalars()
        if any(v not in known for v in versions): raise MigrationError('unsupported_schema_version')
    if not inspect(c).has_table(ledger.name): return {}
    rows = {r['version']: dict(r) for r in c.execute(select(ledger)).mappings()}
    for version, row in rows.items():
        if version not in known: raise MigrationError('unknown_migration:' + str(version))
        if row['checksum'] != known[version].checksum or row['name'] != known[version].name:
            raise MigrationError('migration_checksum_mismatch:' + str(version))
    return rows

def status(engine, migrations=None):
    migrations = definitions() if migrations is None else migrations
    with engine.connect() as c:
        rows = audit(c, migrations)
        result = []
        for m in migrations:
            row = rows.get(m.version)
            if row and row['state'] == 'applied': m.verify(c)
            result.append(dict(version=m.version, name=m.name, checksum=m.checksum,
                               state=row['state'] if row else 'pending', batch_id=row['batch_id'] if row else None,
                               error_code=row['error_code'] if row else None))
        return result

def upgrade(engine, *, target=None, lock_timeout=30, migrations=None, after_statement=None):
    migrations = definitions() if migrations is None else migrations
    if [m.version for m in migrations] != sorted({m.version for m in migrations}): raise MigrationError('invalid_migration_order')
    target = migrations[-1].version if target is None else target
    if target not in {m.version for m in migrations}: raise MigrationError('unknown_target_version')
    after_statement = after_statement or (lambda version, index: None)
    batch = str(uuid.uuid4())
    with migration_lock(engine, lock_timeout) as c:
        rows = audit(c, migrations)  # Check historical checksums BEFORE any DDL.
        if any(v > target for v in rows): raise MigrationError('downgrade_not_supported')
        metadata.create_all(c); c.commit()
        for m in migrations:
            if m.version > target: break
            if rows.get(m.version, {}).get('state') == 'applied': m.verify(c); continue
            started = now(c)
            values = dict(version=m.version, name=m.name, checksum=m.checksum, state='applying',
                          batch_id=batch, started_at=started, applied_at=None, error_code=None)
            if m.version in rows: c.execute(update(ledger).where(ledger.c.version == m.version).values(**values))
            else: c.execute(insert(ledger).values(**values))
            c.execute(insert(attempts).values(version=m.version, batch_id=batch, state='applying', started_at=started))
            c.commit()
            try:
                m.apply(c, after_statement); m.verify(c)
                finished = now(c)
                c.execute(update(ledger).where(ledger.c.version == m.version).values(state='applied', applied_at=finished))
                c.execute(update(attempts).where(attempts.c.version == m.version, attempts.c.batch_id == batch).values(state='applied', finished_at=finished))
                # Keep legacy schema_versions a singleton. Old API health uses scalar_one().
                # Incremental versions live exclusively in hosting_schema_migrations.
                c.commit()
            except BaseException as exc:
                c.rollback()
                # Record safe error codes, never SQL/URLs/credentials. Abrupt death leaves applying.
                code = str(exc)[:100] if isinstance(exc, MigrationError) else type(exc).__name__
                c.execute(update(ledger).where(ledger.c.version == m.version).values(state='failed', error_code=code))
                c.execute(update(attempts).where(attempts.c.version == m.version, attempts.c.batch_id == batch).values(state='failed', error_code=code, finished_at=now(c)))
                c.commit()
                raise
    return dict(batch_id=batch, migrations=status(engine, migrations))

def require_runtime_schema(engine):
    rows = status(engine)
    if any(row['state'] != 'applied' for row in rows):
        raise MigrationError('schema_upgrade_required')
