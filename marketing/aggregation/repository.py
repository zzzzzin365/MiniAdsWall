"""Bounded MySQL claims, consistent snapshots and atomic fenced publication."""
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pymysql

from .attribution import AggregationError, RULE, money
from .metrics import FIELDS, day, zero

ROOT = Path(__file__).resolve().parents[2]
BATCH_LIMIT = 25
BACKLOG_LIMIT = 10000
MAX_ATTEMPTS = 5
DEADLINE_SECONDS = 86400


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decoded(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


class Repository:
    def __init__(self, url=None, socket=None, lease_seconds=120, max_rows=100000):
        url = url or os.environ.get('MARKETING_WORKER_MYSQL_URL', '')
        parsed = urlsplit(url)
        if parsed.scheme not in ('mysql', 'mysql+pymysql') or not parsed.hostname or not parsed.path.strip('/'):
            raise AggregationError('marketing_worker_mysql_url_required', True)
        query = parse_qs(parsed.query)
        if set(query) - {'unix_socket'}:
            raise AggregationError('unsupported_worker_mysql_option', True)
        self.options = dict(host=parsed.hostname, port=parsed.port or 3306, user=unquote(parsed.username or ''),
                            password=unquote(parsed.password or ''), database=unquote(parsed.path[1:]), charset='utf8mb4',
                            cursorclass=pymysql.cursors.DictCursor, autocommit=False, connect_timeout=5, read_timeout=20, write_timeout=20)
        socket = socket or os.environ.get('MARKETING_WORKER_MYSQL_SOCKET') or query.get('unix_socket', [None])[0]
        if socket:
            self.options['unix_socket'] = socket
        if not 1 <= lease_seconds <= 600 or not 1 <= max_rows <= 1000000:
            raise AggregationError('invalid_worker_limits', True)
        self.lease_seconds, self.max_rows = lease_seconds, max_rows

    @contextmanager
    def connection(self, isolation='READ COMMITTED'):
        c = pymysql.connect(**self.options)
        try:
            with c.cursor() as q:
                q.execute("SET time_zone='+00:00'")
                q.execute('SET SESSION innodb_lock_wait_timeout=5')
                q.execute('SET TRANSACTION ISOLATION LEVEL ' + isolation)
            yield c
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def require_schema(self):
        with self.connection() as c, c.cursor() as q:
            q.execute('SELECT version,name,checksum,state FROM marketing_schema_migrations ORDER BY version')
            rows = {r['version']: r for r in q.fetchall()}
            for version, name in ((10, '010_marketing_events.sql'), (11, '011_marketing_ingestion.sql'), (12, '012_marketing_aggregation.sql')):
                row = rows.get(version)
                path = ROOT / 'apps/mini-ad-wall/server/migrations' / name
                if not row or row['state'] != 'applied' or row['name'] != name or row['checksum'] != hashlib.sha256(path.read_bytes()).hexdigest():
                    raise AggregationError('marketing_migration_required', True)

    @staticmethod
    def _failed_job(q, task, code):
        if task['kind'] == 'marketing.rebuild':
            q.execute("UPDATE marketing_rebuild_jobs SET status='failed',result=%s,finished_at=UTC_TIMESTAMP(3) WHERE id=%s",
                      (canonical({'error_code': code}), task['aggregate_id']))

    def claim(self, environment, limit=BATCH_LIMIT):
        if environment not in ('synthetic', 'sandbox', 'production') or not 1 <= limit <= BATCH_LIMIT:
            raise AggregationError('invalid_claim_scope', True)
        with self.connection() as c, c.cursor() as q:
            # Failed tasks remain inspectable and count toward the admission limit.
            q.execute("""SELECT * FROM marketing_outbox WHERE environment=%s AND
                (status='pending' OR (status='running' AND lease_until<=UTC_TIMESTAMP(3)))
                AND (attempt>=%s OR created_at<UTC_TIMESTAMP(3)-INTERVAL %s SECOND)
                ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""", (environment, MAX_ATTEMPTS, DEADLINE_SECONDS, BATCH_LIMIT))
            for task in q.fetchall():
                q.execute("UPDATE marketing_outbox SET status='failed',lease_until=NULL,error_code='retry_budget_exhausted' WHERE id=%s", (task['id'],))
                self._failed_job(q, task, 'retry_budget_exhausted')
            eligible = "((status='pending' AND next_at<=UTC_TIMESTAMP(3)) OR (status='running' AND lease_until<=UTC_TIMESTAMP(3))) AND attempt<%s AND created_at>=UTC_TIMESTAMP(3)-INTERVAL %s SECOND"
            q.execute(f'SELECT * FROM marketing_outbox WHERE environment=%s AND {eligible} ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED',
                      (environment, MAX_ATTEMPTS, DEADLINE_SECONDS))
            first = q.fetchone()
            if not first:
                c.commit()
                return []
            q.execute(f'SELECT * FROM marketing_outbox WHERE environment=%s AND brand_id=%s AND {eligible} ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED',
                      (environment, first['brand_id'], MAX_ATTEMPTS, DEADLINE_SECONDS, limit))
            tasks = q.fetchall()
            for task in tasks:
                q.execute("UPDATE marketing_outbox SET status='running',lease_epoch=lease_epoch+1,attempt=attempt+1,lease_until=UTC_TIMESTAMP(3)+INTERVAL %s SECOND,error_code=NULL WHERE id=%s",
                          (self.lease_seconds, task['id']))
                task['lease_epoch'] += 1
                task['attempt'] += 1
                task['payload'] = decoded(task['payload'])
            c.commit()
            return tasks

    def _bounded(self, q, sql, args):
        q.execute(sql + ' LIMIT %s', (*args, self.max_rows + 1))
        rows = q.fetchall()
        if len(rows) > self.max_rows:
            raise AggregationError('snapshot_row_limit_exceeded', True)
        return rows

    def snapshot(self, brand, environment, rule=RULE):
        with self.connection() as c, c.cursor() as q:
            q.execute('INSERT IGNORE INTO marketing_input_epochs(brand_id,environment) VALUES (%s,%s)', (brand, environment))
            q.execute('INSERT IGNORE INTO marketing_aggregation_state(brand_id,environment,rule_version) VALUES (%s,%s,%s)', (brand, environment, rule))
            c.commit()
        with self.connection('REPEATABLE READ') as c, c.cursor() as q:
            q.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            q.execute('SELECT epoch FROM marketing_input_epochs WHERE brand_id=%s AND environment=%s', (brand, environment))
            epoch = q.fetchone()['epoch']
            q.execute('SELECT generation FROM marketing_aggregation_state WHERE brand_id=%s AND environment=%s AND rule_version=%s', (brand, environment, rule))
            generation = q.fetchone()['generation']
            q.execute('SELECT UTC_TIMESTAMP(3) AS now')
            cutoff = q.fetchone()['now']
            events = self._bounded(q, """SELECT e.*,v.status,v.revision AS validation_revision,
                d.visitor_session_id AS session,d.campaign_id AS campaign,d.creative_id AS creative,d.creative_version,
                a.product_id AS product FROM marketing_events e
                JOIN marketing_event_validation v ON v.event_id=e.id AND v.revision=(SELECT MAX(revision) FROM marketing_event_validation WHERE event_id=e.id)
                LEFT JOIN marketing_serving_decisions d ON d.impression_id=e.impression_id AND d.environment=e.environment AND d.brand_id=e.brand_id
                LEFT JOIN marketing_campaigns a ON a.id=d.campaign_id
                WHERE e.brand_id=%s AND e.environment=%s ORDER BY e.received_at,e.id""", (brand, environment))
            for event in events:
                event['payload'] = decoded(event['payload'])
            spend = self._bounded(q, 'SELECT * FROM marketing_spend_ledger WHERE brand_id=%s AND environment=%s ORDER BY occurred_at,id', (brand, environment))
            heads = self._bounded(q, 'SELECT * FROM marketing_metric_heads WHERE brand_id=%s AND environment=%s AND rule_version=%s', (brand, environment, rule))
            old_orders = self._bounded(q, """SELECT a.* FROM marketing_order_attribution_heads h JOIN marketing_order_attributions a
                ON a.source=h.source AND a.order_id=h.order_id AND a.environment=h.environment AND a.revision=h.revision
                WHERE a.brand_id=%s AND a.environment=%s""", (brand, environment))
            for row in old_orders:
                row['evidence'] = decoded(row['evidence'])
                if row['rule_version'] != rule:
                    raise AggregationError('active_attribution_rule_mismatch', True)
            c.commit()
        return dict(brand=brand, environment=environment, rule=rule, epoch=epoch, generation=generation,
                    cutoff=cutoff, events=events, spend=spend, heads=heads, old_orders=old_orders)

    def publish(self, snapshot, tasks, orders, partitions, rebuild_ranges=(), before_commit=None):
        s = snapshot
        if len(partitions) > self.max_rows:
            raise AggregationError('partition_limit_exceeded', True)
        old_orders = {(r['source'], r['order_id']): r for r in s['old_orders']}
        old_heads = {(r['campaign_id'], r['creative_id'], r['creative_version'], r['day']): r for r in s['heads']}
        # Include zero replacements after an attribution moves to another creative/day.
        keys = sorted(set(partitions) | set(old_heads))
        result = {'orders': len(orders), 'attributed': sum(o['status'] == 'attributed' for o in orders.values()),
                  'unattributed': sum(o['status'] == 'unattributed' for o in orders.values()),
                  'pending': sum(o['status'] == 'pending' for o in orders.values()),
                  'net_revenue_minor': money(sum(o['net_revenue_minor'] for o in orders.values())),
                  'spend_minor': money(sum(int(r['amount_minor']) for r in s['spend'])),
                  'input_epoch': s['epoch'], 'generation': s['generation'] + 1, 'written_partitions': 0, 'deferred_partitions': 0}
        result['raw_event_count'] = len(s['events'])
        result['raw_spend_count'] = len(s['spend'])
        result['input_hash'] = fingerprint({'events': [(e['id'], e['occurred_at'], e['payload_hash'], e['validation_revision'], e['status']) for e in s['events']],
                                            'spend': [(e['id'], e['occurred_at'], e['payload_hash']) for e in s['spend']]})
        result['range_totals'] = [dict(starts_on=start.isoformat(), ends_before=end.isoformat(),
                                      **{field: sum(value[field] for key, value in partitions.items() if start <= key[3] < end) for field in FIELDS})
                                  for start, end in rebuild_ranges]
        with self.connection() as c, c.cursor() as q:
            q.execute('SELECT epoch FROM marketing_input_epochs WHERE brand_id=%s AND environment=%s FOR UPDATE', (s['brand'], s['environment']))
            if q.fetchone()['epoch'] != s['epoch']:
                raise AggregationError('inputs_changed')
            q.execute('SELECT generation FROM marketing_aggregation_state WHERE brand_id=%s AND environment=%s AND rule_version=%s FOR UPDATE', (s['brand'], s['environment'], s['rule']))
            if q.fetchone()['generation'] != s['generation']:
                raise AggregationError('publication_changed')
            for task in sorted(tasks, key=lambda t: t['id']):
                q.execute("SELECT lease_epoch,status,(lease_until>UTC_TIMESTAMP(3) AND created_at>=UTC_TIMESTAMP(3)-INTERVAL %s SECOND) AS live FROM marketing_outbox WHERE id=%s FOR UPDATE", (DEADLINE_SECONDS, task['id']))
                row = q.fetchone()
                if not row or row['status'] != 'running' or row['lease_epoch'] != task['lease_epoch'] or not row['live']:
                    raise AggregationError('lease_lost')
            for key in sorted(set(orders) | set(old_orders)):
                order = orders.get(key)
                if order is None:
                    order = dict(source=key[0], order_id=key[1], status='unattributed', click_id=None, net_revenue_minor=0,
                                 evidence={'reason': 'no_valid_payment', 'rule': s['rule']}, link=None)
                old = old_orders.get(key)
                changed = not old or any(order[k] != old[k] for k in ('click_id', 'status', 'net_revenue_minor', 'evidence'))
                revision = (int(old['revision']) if old else 0) + int(changed)
                if changed:
                    q.execute('INSERT INTO marketing_order_attributions(source,order_id,revision,brand_id,environment,click_id,rule_version,net_revenue_minor,status,evidence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                              (*key, revision, s['brand'], s['environment'], order['click_id'], s['rule'], order['net_revenue_minor'], order['status'], canonical(order['evidence'])))
                    q.execute('INSERT INTO marketing_order_attribution_heads(source,order_id,environment,revision) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE revision=VALUES(revision)', (*key, s['environment'], revision))
                link = order['link']
                if link:
                    q.execute('''INSERT INTO marketing_click_orders(source,order_id,brand_id,environment,rule_version,attribution_revision,click_id,click_at,paid_at,campaign_id,creative_id,creative_version,net_revenue_minor,refunded_minor)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE attribution_revision=VALUES(attribution_revision),click_id=VALUES(click_id),click_at=VALUES(click_at),paid_at=VALUES(paid_at),campaign_id=VALUES(campaign_id),creative_id=VALUES(creative_id),creative_version=VALUES(creative_version),net_revenue_minor=VALUES(net_revenue_minor),refunded_minor=VALUES(refunded_minor)''',
                              (*key, s['brand'], s['environment'], s['rule'], revision, *(link[k] for k in ('click_id','click_at','paid_at','campaign_id','creative_id','creative_version','net_revenue_minor','refunded_minor'))))
                else:
                    q.execute('DELETE FROM marketing_click_orders WHERE source=%s AND order_id=%s AND environment=%s AND rule_version=%s', (*key, s['environment'], s['rule']))
            for key in keys:
                values, old = partitions.get(key, zero()), old_heads.get(key)
                digest = fingerprint(values)
                forced = any(start <= key[3] < end for start, end in rebuild_ranges)
                changed = not old or old['fingerprint'] != digest
                if not changed and not forced:
                    q.execute('DELETE FROM marketing_pending_partitions WHERE environment=%s AND campaign_id=%s AND creative_id=%s AND creative_version=%s AND day=%s AND rule_version=%s', (s['environment'], *key, s['rule']))
                    continue
                # Last 30 Shanghai calendar days, inclusive of today. Explicit rebuilds
                # select older days without changing the automatic observation window.
                # The ingress clock permits +5 minutes, possibly across midnight.
                recent = day(s['cutoff']) - timedelta(days=29) <= key[3] <= day(s['cutoff'] + timedelta(minutes=5))
                if not recent and not forced:
                    q.execute('''INSERT INTO marketing_pending_partitions(brand_id,environment,campaign_id,creative_id,creative_version,day,rule_version,input_epoch,reason)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'outside_auto_window') ON DUPLICATE KEY UPDATE input_epoch=VALUES(input_epoch),reason=VALUES(reason)''',
                              (s['brand'], s['environment'], *key, s['rule'], s['epoch']))
                    result['deferred_partitions'] += 1
                    continue
                revision = (int(old['revision']) if old else 0) + 1
                q.execute('''INSERT INTO marketing_daily_metrics(brand_id,environment,campaign_id,creative_id,creative_version,day,rule_version,revision,impressions,clicks,paid_orders,refunded_orders,revenue_minor,spend_minor,received_cutoff)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                          (s['brand'], s['environment'], *key, s['rule'], revision, *(values[k] for k in FIELDS), s['cutoff']))
                q.execute('''INSERT INTO marketing_metric_heads(brand_id,environment,campaign_id,creative_id,creative_version,day,rule_version,revision,fingerprint,input_epoch)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE revision=VALUES(revision),fingerprint=VALUES(fingerprint),input_epoch=VALUES(input_epoch)''',
                          (s['brand'], s['environment'], *key, s['rule'], revision, digest, s['epoch']))
                q.execute('DELETE FROM marketing_pending_partitions WHERE environment=%s AND campaign_id=%s AND creative_id=%s AND creative_version=%s AND day=%s AND rule_version=%s', (s['environment'], *key, s['rule']))
                result['written_partitions'] += 1
            q.execute('UPDATE marketing_aggregation_state SET generation=generation+1,input_epoch=%s,received_cutoff=%s WHERE brand_id=%s AND environment=%s AND rule_version=%s', (s['epoch'], s['cutoff'], s['brand'], s['environment'], s['rule']))
            for task in tasks:
                q.execute("UPDATE marketing_outbox SET status='done',lease_until=NULL,error_code=NULL WHERE id=%s AND lease_epoch=%s AND status='running' AND lease_until>UTC_TIMESTAMP(3) AND created_at>=UTC_TIMESTAMP(3)-INTERVAL %s SECOND", (task['id'], task['lease_epoch'], DEADLINE_SECONDS))
                if q.rowcount != 1:
                    raise AggregationError('lease_lost')
                if task['kind'] == 'marketing.rebuild':
                    q.execute("UPDATE marketing_rebuild_jobs SET status='done',result=%s,finished_at=UTC_TIMESTAMP(3) WHERE id=%s", (canonical(result), task['aggregate_id']))
            if before_commit:
                before_commit()
            c.commit()
        return result

    def fail(self, tasks, error):
        code = error.code if isinstance(error, AggregationError) else 'aggregation_storage_error'
        permanent = isinstance(error, AggregationError) and error.permanent
        with self.connection() as c, c.cursor() as q:
            for task in tasks:
                q.execute("SELECT attempt,created_at,UTC_TIMESTAMP(3) AS now FROM marketing_outbox WHERE id=%s AND status='running' AND lease_epoch=%s AND lease_until>UTC_TIMESTAMP(3) FOR UPDATE", (task['id'], task['lease_epoch']))
                row = q.fetchone()
                if not row:
                    continue
                failed = permanent or row['attempt'] >= MAX_ATTEMPTS or (row['now'] - row['created_at']).total_seconds() >= DEADLINE_SECONDS
                q.execute("UPDATE marketing_outbox SET status=%s,lease_until=NULL,error_code=%s,next_at=UTC_TIMESTAMP(3)+INTERVAL %s SECOND WHERE id=%s AND lease_epoch=%s",
                          ('failed' if failed else 'pending', code, min(60, 2 ** row['attempt']), task['id'], task['lease_epoch']))
                if failed:
                    self._failed_job(q, task, code)
            c.commit()

    def monitor(self, environment):
        if environment not in ('synthetic', 'sandbox', 'production'):
            raise AggregationError('invalid_monitor_scope', True)
        with self.connection() as c, c.cursor() as q:
            q.execute('SELECT status,COUNT(*) AS count,MAX(TIMESTAMPDIFF(SECOND,created_at,UTC_TIMESTAMP(3))) AS oldest_age_seconds FROM marketing_outbox WHERE environment=%s GROUP BY status', (environment,))
            queue = q.fetchall()
            q.execute("SELECT error_code,COUNT(*) AS count FROM marketing_outbox WHERE environment=%s AND status='failed' GROUP BY error_code", (environment,))
            failures = q.fetchall()
            q.execute('''SELECT v.status,COUNT(*) AS count FROM marketing_events e JOIN marketing_event_validation v ON v.event_id=e.id
                AND v.revision=(SELECT MAX(revision) FROM marketing_event_validation WHERE event_id=e.id) WHERE e.environment=%s GROUP BY v.status''', (environment,))
            validation = q.fetchall()
            q.execute('SELECT COUNT(*) AS count FROM marketing_pending_partitions WHERE environment=%s', (environment,))
            deferred = q.fetchone()['count']
            q.execute('''SELECT COUNT(*) AS count FROM marketing_spend_isolation s WHERE EXISTS
                (SELECT 1 FROM marketing_outbox o WHERE o.kind='marketing.spend-isolated' AND o.aggregate_id=s.id AND o.environment=%s)''', (environment,))
            isolated_spend = q.fetchone()['count']
            return dict(environment=environment, queue=queue, failures=failures, validation=validation,
                        pending_partitions=deferred, isolated_spend=isolated_spend, backlog_limit_per_brand=BACKLOG_LIMIT,
                        max_attempts=MAX_ATTEMPTS, deadline_seconds=DEADLINE_SECONDS)
