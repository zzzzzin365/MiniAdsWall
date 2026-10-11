"""Deterministic last-click attribution over a committed input snapshot."""
from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta

RULE = 'last-click-v1'
MAX_MINOR = 9007199254740991


class AggregationError(Exception):
    def __init__(self, code, permanent=False):
        super().__init__(code)
        self.code, self.permanent = code, permanent


def money(value):
    value = int(value)
    if not 0 <= value <= MAX_MINOR:
        raise AggregationError('aggregate_amount_out_of_range', True)
    return value


def iso(value):
    return value.isoformat(timespec='milliseconds') + 'Z' if value else None


def attribute(events, rule=RULE):
    if rule != RULE:
        raise AggregationError('unsupported_attribution_rule', True)
    clicks, orders = defaultdict(list), defaultdict(list)
    for event in events:
        if event['status'] == 'quarantined':
            continue
        if event['type'] == 'click' and event['status'] == 'valid':
            if not all(event.get(k) for k in ('click_id', 'session', 'product', 'campaign', 'creative', 'creative_version')):
                raise AggregationError('invalid_click_scope', True)
            key = (event['brand_id'], event['environment'], event['session'], event['product'])
            clicks[key].append(event)
        elif event['type'] in ('order_paid', 'order_refunded'):
            orders[(event['source'], event['order_id'])].append(event)
    indexes = {}
    for key, group in clicks.items():
        group.sort(key=lambda e: (e['occurred_at'], e['click_id']))
        indexes[key] = ([e['occurred_at'] for e in group], group)
    results = {}
    for key, group in sorted(orders.items()):
        paid = [e for e in group if e['type'] == 'order_paid' and e['status'] == 'valid']
        if len(paid) > 1:
            raise AggregationError('multiple_valid_payments', True)
        if not paid:
            results[key] = {'source': key[0], 'order_id': key[1], 'status': 'pending', 'click_id': None,
                            'net_revenue_minor': 0, 'evidence': {'reason': 'payment_not_received', 'rule': rule}, 'link': None}
            continue
        paid = paid[0]
        refunds = sorted((e for e in group if e['type'] == 'order_refunded' and e['status'] == 'valid'), key=lambda e: e['id'])
        if any(e['product_id'] != paid['product_id'] for e in refunds):
            raise AggregationError('refund_product_mismatch', True)
        refunded = money(sum(int(e['amount_minor']) for e in refunds))
        amount = money(paid['amount_minor'])
        if refunded > amount:
            raise AggregationError('refund_exceeds_payment', True)
        session = paid['payload'].get('visitor_session_id')
        candidate, reason = None, 'session_missing' if not session else 'no_click_in_window'
        index = indexes.get((paid['brand_id'], paid['environment'], session, paid['product_id']))
        if index:
            at = bisect_right(index[0], paid['occurred_at']) - 1
            if at >= 0 and index[1][at]['occurred_at'] >= paid['occurred_at'] - timedelta(days=7):
                candidate, reason = index[1][at], 'last_click'
        link = None
        if candidate:
            link = {'click_id': candidate['click_id'], 'click_at': candidate['occurred_at'], 'paid_at': paid['occurred_at'],
                    'campaign_id': candidate['campaign'], 'creative_id': candidate['creative'],
                    'creative_version': int(candidate['creative_version']), 'net_revenue_minor': amount - refunded, 'refunded_minor': refunded}
        evidence = {'reason': reason, 'rule': rule, 'paid_event_id': paid['id'], 'paid_at': iso(paid['occurred_at']),
                    'paid_amount_minor': amount, 'refund_event_ids': [e['id'] for e in refunds], 'refunded_minor': refunded,
                    'product_id': paid['product_id'], 'click_at': iso(candidate['occurred_at']) if candidate else None,
                    'campaign_id': candidate['campaign'] if candidate else None, 'creative_id': candidate['creative'] if candidate else None,
                    'creative_version': int(candidate['creative_version']) if candidate else None}
        results[key] = {'source': key[0], 'order_id': key[1], 'status': 'attributed' if candidate else 'unattributed',
                        'click_id': candidate['click_id'] if candidate else None, 'net_revenue_minor': amount - refunded,
                        'evidence': evidence, 'link': link}
    return results
