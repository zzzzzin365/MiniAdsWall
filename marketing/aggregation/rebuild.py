"""Explicit, auditable reconstruction request; no deletion of source or revisions."""
import argparse
import json
import uuid
from datetime import date

from .attribution import AggregationError, RULE
from .repository import BACKLOG_LIMIT, Repository, canonical


def validate_range(start, end):
    try:
        starts, ends = date.fromisoformat(start), date.fromisoformat(end)
        if starts.isoformat() != start or ends.isoformat() != end or not 0 < (ends - starts).days <= 366:
            raise ValueError()
    except (TypeError, ValueError):
        raise AggregationError('invalid_rebuild_range', True) from None
    return starts, ends


def request(repository, brand, environment, start, end, actor, rule=RULE, request_id=None):
    starts, ends = validate_range(start, end)
    if rule != RULE:
        raise AggregationError('unsupported_attribution_rule', True)
    if environment not in ('synthetic', 'sandbox', 'production') or not isinstance(brand, str) or not 1 <= len(brand) <= 100 or not isinstance(actor, str) or not 1 <= len(actor) <= 190:
        raise AggregationError('invalid_rebuild_scope', True)
    identifier = request_id or str(uuid.uuid4())
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
        raise AggregationError('invalid_rebuild_id', True)
    payload = {'rule_version': rule, 'starts_on': start, 'ends_before': end}
    with repository.connection() as c, c.cursor() as q:
        q.execute('SELECT * FROM marketing_rebuild_jobs WHERE id=%s', (identifier,))
        old = q.fetchone()
        if old:
            expected = (brand, environment, rule, starts, ends, actor)
            if tuple(old[k] for k in ('brand_id', 'environment', 'rule_version', 'starts_on', 'ends_before', 'requested_by')) != expected:
                raise AggregationError('rebuild_id_conflict', True)
            return identifier
        # Rebuild scheduling is a privileged CLI action using an operator's DB
        # account, not the worker credential. Brand scope is still explicit.
        q.execute('INSERT INTO marketing_input_epochs(brand_id,environment,epoch) VALUES (%s,%s,1) ON DUPLICATE KEY UPDATE epoch=epoch+1', (brand, environment))
        q.execute("SELECT COUNT(*) AS n FROM (SELECT id FROM marketing_outbox WHERE brand_id=%s AND environment=%s AND status<>'done' LIMIT %s) bounded", (brand, environment, BACKLOG_LIMIT))
        if q.fetchone()['n'] >= BACKLOG_LIMIT:
            raise AggregationError('marketing_backlog_full')
        # The clock lock makes concurrent same-ID requests recheck after waiting.
        q.execute('SELECT * FROM marketing_rebuild_jobs WHERE id=%s', (identifier,))
        old = q.fetchone()
        if old:
            if tuple(old[k] for k in ('brand_id', 'environment', 'rule_version', 'starts_on', 'ends_before', 'requested_by')) != (brand, environment, rule, starts, ends, actor):
                raise AggregationError('rebuild_id_conflict', True)
            c.rollback()
            return identifier
        try:
            q.execute('INSERT INTO marketing_rebuild_jobs(id,brand_id,environment,rule_version,starts_on,ends_before,requested_by) VALUES (%s,%s,%s,%s,%s,%s,%s)', (identifier, brand, environment, rule, starts, ends, actor))
        except Exception as error:
            if error.args and error.args[0] == 1062:
                raise AggregationError('rebuild_id_conflict', True) from None
            raise
        q.execute("INSERT INTO marketing_outbox(kind,aggregate_id,brand_id,environment,payload) VALUES ('marketing.rebuild',%s,%s,%s,%s)", (identifier, brand, environment, canonical(payload)))
        c.commit()
    return identifier


def main():
    parser = argparse.ArgumentParser(description='Rebuild Shanghai calendar-day partitions [start,end), maximum 366 days per job.')
    parser.add_argument('--brand', required=True)
    parser.add_argument('--environment', required=True, choices=('synthetic', 'sandbox', 'production'))
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--rule', required=True, choices=(RULE,))
    parser.add_argument('--request-id', help='Repeat the same ID and payload to replay; change ID for a new revision.')
    args = parser.parse_args()
    repository = Repository()
    repository.require_schema()
    identifier = request(repository, args.brand, args.environment, args.start, args.end, args.actor, args.rule, args.request_id)
    print(json.dumps({'job_id': identifier, 'environment': args.environment, 'rule_version': args.rule}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error_code': error.code if isinstance(error, AggregationError) else 'aggregation_storage_error'}))
        raise SystemExit(1)
