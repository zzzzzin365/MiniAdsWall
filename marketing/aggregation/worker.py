"""python -m marketing.aggregation.worker --environment synthetic [--once]."""
import argparse
import json
import signal
import threading

from .attribution import AggregationError, RULE, attribute
from .metrics import aggregate
from .repository import Repository
from .rebuild import validate_range

KINDS = {'marketing.event', 'marketing.spend', 'marketing.spend-isolated', 'marketing.coverage', 'marketing.rebuild'}


def run_once(repository, environment, rule=RULE, hooks=None):
    if rule != RULE:
        raise AggregationError('unsupported_attribution_rule', True)
    hooks = hooks or {}
    tasks = repository.claim(environment)
    if not tasks:
        return {'status': 'idle'}
    invalid = [t for t in tasks if t['kind'] not in KINDS]
    for task in invalid:
        repository.fail([task], AggregationError('unknown_outbox_kind', True))
    tasks = [t for t in tasks if t not in invalid]
    if not tasks:
        return {'status': 'failed', 'error_code': 'unknown_outbox_kind'}
    try:
        snapshot = repository.snapshot(tasks[0]['brand_id'], environment, rule)
        ids = {e['id']: e for e in snapshot['events']}
        spend_ids = {s['id'] for s in snapshot['spend']}
        ranges, accepted = [], []
        for task in tasks:
            try:
                payload = task['payload']
                if not isinstance(payload, dict):
                    raise AggregationError('invalid_outbox_payload', True)
                if task['kind'] == 'marketing.event':
                    event = ids.get(task['aggregate_id'])
                    if not event or payload.get('event_id') != task['aggregate_id'] or payload.get('validation_revision') != task['revision'] or event['validation_revision'] < task['revision']:
                        raise AggregationError('event_outbox_mismatch', True)
                elif task['kind'] == 'marketing.spend':
                    if task['aggregate_id'] not in spend_ids or payload.get('entry_id') != task['aggregate_id']:
                        raise AggregationError('spend_outbox_mismatch', True)
                elif task['kind'] == 'marketing.rebuild':
                    if payload.get('rule_version') != rule:
                        raise AggregationError('unsupported_attribution_rule', True)
                    ranges.append(validate_range(payload.get('starts_on'), payload.get('ends_before')))
                accepted.append(task)
            except AggregationError as error:
                repository.fail([task], error)
        tasks = accepted
        if not tasks:
            return {'status': 'failed', 'error_code': 'invalid_outbox_payload'}
        orders = attribute(snapshot['events'], rule)
        partitions = aggregate(snapshot['events'], snapshot['spend'], orders)
        if hooks.get('before_publish'):
            hooks['before_publish']()
        result = repository.publish(snapshot, tasks, orders, partitions, ranges, hooks.get('before_commit'))
        if hooks.get('after_commit'):
            hooks['after_commit']()
        return {'status': 'done', 'task_ids': [t['id'] for t in tasks], **result}
    except Exception as error:
        repository.fail(tasks, error)
        return {'status': 'failed' if isinstance(error, AggregationError) and error.permanent else 'retry',
                'error_code': error.code if isinstance(error, AggregationError) else 'aggregation_storage_error'}


def main():
    parser = argparse.ArgumentParser(description='Consume accepted marketing events even if ingress is disabled.')
    parser.add_argument('--environment', required=True, choices=('synthetic', 'sandbox', 'production'))
    parser.add_argument('--rule', default=RULE, choices=(RULE,))
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--drain', action='store_true', help='Exit at idle, retries remain observable; bounded to --max-batches.')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--max-batches', type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.max_batches <= 1000 or sum((args.once, args.drain, args.status)) > 1:
        parser.error('invalid worker mode or batch bound')
    repository = Repository()
    repository.require_schema()
    if args.status:
        print(json.dumps(repository.monitor(args.environment)))
        return
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    count = 0
    while not stop.is_set():
        result = run_once(repository, args.environment, args.rule)
        print(json.dumps(result), flush=True)
        count += 1
        if args.once or (args.drain and (result['status'] == 'idle' or count >= args.max_batches)):
            break
        if result['status'] in ('idle', 'retry', 'failed'):
            stop.wait(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Credentials and raw driver exception text must never reach logs.
        print(json.dumps({'error_code': error.code if isinstance(error, AggregationError) else 'aggregation_storage_error'}))
        raise SystemExit(1)
