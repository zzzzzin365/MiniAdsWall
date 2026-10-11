"""Pay-day aggregates; click cohorts are persisted separately in click_orders."""
from collections import defaultdict
from datetime import timezone
from zoneinfo import ZoneInfo
from .attribution import AggregationError, MAX_MINOR

FIELDS = ('impressions', 'clicks', 'paid_orders', 'refunded_orders', 'revenue_minor', 'spend_minor')
REPORT_ZONE = ZoneInfo('Asia/Shanghai')


def day(value):
    return value.replace(tzinfo=timezone.utc).astimezone(REPORT_ZONE).date()


def zero():
    return dict.fromkeys(FIELDS, 0)


def aggregate(events, spend, orders):
    partitions = defaultdict(zero)
    for event in events:
        if event['status'] != 'valid' or event['type'] not in ('impression', 'click'):
            continue
        if not all(event.get(k) for k in ('campaign', 'creative', 'creative_version')):
            raise AggregationError('invalid_delivery_scope', True)
        key = (event['campaign'], event['creative'], int(event['creative_version']), day(event['occurred_at']))
        partitions[key]['impressions' if event['type'] == 'impression' else 'clicks'] += 1
    for order in orders.values():
        link = order['link']
        if not link:
            continue
        key = (link['campaign_id'], link['creative_id'], link['creative_version'], day(link['paid_at']))
        row = partitions[key]
        row['paid_orders'] += 1
        row['refunded_orders'] += int(link['refunded_minor'] > 0)
        row['revenue_minor'] += link['net_revenue_minor']
    for entry in spend:
        key = (entry['campaign_id'], entry['creative_id'], int(entry['creative_version']), day(entry['occurred_at']))
        partitions[key]['spend_minor'] += int(entry['amount_minor'])
    for row in partitions.values():
        if any(abs(value) > MAX_MINOR for value in row.values()) or row['spend_minor'] < 0:
            raise AggregationError('aggregate_amount_out_of_range', True)
    return dict(partitions)
