from datetime import date, datetime, timedelta

import unittest

from marketing.aggregation.attribution import AggregationError, attribute
from marketing.aggregation.metrics import aggregate, day
from marketing.aggregation.rebuild import validate_range

AT = datetime(2026, 10, 9, 16, 30)


def click(identifier, at=AT, **values):
    return dict(id=identifier, click_id=identifier, type='click', status='valid', brand_id='brand',
                environment='synthetic', source='browser', session='session', product='product', campaign='campaign',
                creative='creative', creative_version=1, occurred_at=at, payload={}, **values)


def order(at=AT, session='session', **values):
    base = dict(id='paid', type='order_paid', status='valid', brand_id='brand', environment='synthetic',
                source='shop', order_id='order', product_id='product', occurred_at=at,
                amount_minor=1000, payload={'visitor_session_id': session})
    return {**base, **values}


class MarketingAggregationTest(unittest.TestCase):
    def test_last_click_tie_window_and_scope(self):
        a, b = click('a'), click('b')
        assert attribute([order(), b, a])[('shop', 'order')]['click_id'] == 'b'
        boundary = click('boundary', AT-timedelta(days=7))
        assert attribute([boundary, order()])[('shop', 'order')]['click_id'] == 'boundary'
        excluded = [click('old', AT-timedelta(days=7, milliseconds=1)), click('future', AT+timedelta(milliseconds=1))]
        for field, value in (('session', 'other'), ('product', 'other'), ('brand_id', 'other'), ('environment', 'production'), ('status', 'quarantined')):
            excluded.append({**click(field), field: value})
        outcome = attribute([*excluded, order()])[('shop', 'order')]
        assert outcome['status'] == 'unattributed'
        assert outcome['evidence']['reason'] == 'no_click_in_window'
        assert attribute([a, order(session=None)])[('shop', 'order')]['evidence']['reason'] == 'session_missing'


    def test_refund_latest_validation_and_separate_pay_day_click_cohort(self):
        paid = order(AT+timedelta(days=1))
        refund = order(AT+timedelta(days=2), id='refund', type='order_refunded', amount_minor=200)
        pending = order(id='pending-refund', type='order_refunded', amount_minor=999, status='pending')
        rows = [click('a'), paid, refund, pending]
        attributed = attribute(rows)
        outcome = attributed[('shop', 'order')]
        assert outcome['net_revenue_minor'] == 800
        assert outcome['link']['click_at'] == AT
        assert outcome['link']['paid_at'] == paid['occurred_at']
        metrics = aggregate(rows, [], attributed)
        assert metrics[('campaign', 'creative', 1, date(2026, 10, 10))]['clicks'] == 1
        pay = metrics[('campaign', 'creative', 1, date(2026, 10, 11))]
        assert (pay['clicks'], pay['paid_orders'], pay['refunded_orders'], pay['revenue_minor']) == (0, 1, 1, 800)
        assert day(datetime(2026, 10, 9, 15, 59, 59)) == date(2026, 10, 9)
        assert day(datetime(2026, 10, 9, 16)) == date(2026, 10, 10)
        assert attribute([pending])[('shop', 'order')]['status'] == 'pending'
        with self.assertRaisesRegex(AggregationError, 'refund_exceeds_payment'):
            attribute([paid, {**refund, 'amount_minor': 1001}])


    def test_ordering_and_recomputation_preserve_exact_integer_results(self):
        rows = [click('a'), order(), order(id='refund', type='order_refunded', amount_minor=300)]
        spend = [dict(campaign_id='campaign', creative_id='creative', creative_version=1, occurred_at=AT, amount_minor=100),
                 dict(campaign_id='campaign', creative_id='creative', creative_version=1, occurred_at=AT, amount_minor=-20)]
        for inputs in (rows, list(reversed(rows)), [rows[2], rows[0], rows[1]]):
            assert aggregate(inputs, spend, attribute(inputs)) == aggregate(rows, spend, attribute(rows))
        two_orders = [rows[0], order(), order(order_id='second', id='paid2')]
        results = attribute(two_orders)
        assert len(results) == 2  # One click may have two legitimate attributed orders.
        assert aggregate(two_orders, [], results)[('campaign', 'creative', 1, day(AT))]['paid_orders'] == 2


    def test_explicit_rebuild_range_and_rule(self):
        assert validate_range('2026-10-01', '2026-10-02') == (date(2026, 10, 1), date(2026, 10, 2))
        for start, end in ((None, None), ('20261001', '20261002'), ('2026-10-01', '2026-10-01'), ('2025-01-01', '2026-10-01')):
            with self.assertRaisesRegex(AggregationError, 'invalid_rebuild_range'):
                validate_range(start, end)
        with self.assertRaisesRegex(AggregationError, 'unsupported_attribution_rule'):
            attribute([], 'unknown')
