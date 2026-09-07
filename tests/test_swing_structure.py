import copy
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.market_engine.swing_structure import SwingSettings, SwingStructure
from src.backend import swing_structure_service as service


class SwingTests(unittest.TestCase):
    def engine(self, **kwargs):
        return SwingStructure(SwingSettings(reversal_bps=100, volatility_multiple=.01, major_multiple=3, **kwargs))

    def seed_high(self, engine):
        for t, p in enumerate([10., 10.2, 10.4, 10.2], 1):
            engine.observe(t, p, p, p)
        return next(l for l in engine.active.values() if l['side'] == 'resistance' and l['scale'] == 'local')

    def test_causal_confirmation_and_extreme_geometry(self):
        e = self.engine()
        self.seed_high(e)
        r = next(s for s in e.segments if s['side'] == 'resistance')
        self.assertEqual((r['price'], r['pivot_at'], r['confirmed_at'], r['valid_from']), (10.4, 3, 4, 4))
        self.assertTrue(all(s['confirmed_at'] > s['pivot_at'] for s in e.segments))

    def test_intrabar_order_is_not_invented(self):
        e = self.engine()
        e.observe(1, 10, 10, 10)
        e.observe(2, 11, 9, 9.2)
        self.assertEqual(e.segments, [])
        e.observe(3, 9.3, 9.2, 9.3)
        self.assertTrue(all(s['confirmed_at'] == 3 for s in e.segments))

    def test_small_oscillations_do_not_found_levels(self):
        e = self.engine()
        for t in range(1, 200):
            p = 10 + .01*(t%2)
            e.observe(t,p,p,p)
        self.assertEqual(e.segments, [])

    def test_two_scales_and_no_global_price_cells(self):
        e = self.engine()
        self.seed_high(e)
        self.assertTrue(any(s['scale'] == 'local' for s in e.segments))
        self.assertFalse(any(s['scale'] == 'major' and s['side'] == 'resistance' for s in e.segments))
        e.observe(5, 10, 10, 10)
        self.assertTrue(any(s['scale'] == 'major' and s['side'] == 'resistance' for s in e.segments))

    def test_role_requires_break_then_retest_and_departure(self):
        e = self.engine()
        l = self.seed_high(e)
        e.observe(5,10.5,10.5,10.5)
        self.assertEqual(l['state'],'active')
        e.observe(6,10.6,10.6,10.6)
        self.assertEqual(l['state'],'awaiting_retest')
        self.assertEqual(l['side'],'resistance')
        e.observe(7,10.45,10.4,10.45)
        self.assertEqual(l['state'],'retest_contact')
        e.observe(8,10.6,10.5,10.6)
        self.assertEqual((l['side'],l['state']),('support','active'))
        episodes = [s for s in e.segments if s['level_id'] == l['level_id']]
        self.assertEqual(episodes[-2]['valid_to'], episodes[-1]['valid_from'])

    def test_expiry_is_explicit_and_bounded(self):
        e = self.engine(local_lifetime_seconds=10, major_lifetime_seconds=30)
        l = self.seed_high(e)
        e.observe(20,10,10,10)
        self.assertNotIn(l['level_id'],e.active)
        self.assertGreater(e.counts['expired'],0)

    def test_prefix_semantics_do_not_repaint(self):
        e = self.engine()
        self.seed_high(e)
        earlier = copy.deepcopy(e.segments)
        for t,p in enumerate([10.5,10.6,10.4,10.6],5): e.observe(t,p,p,p)
        def at(rows,t):
            return [{k:v for k,v in r.items() if k != 'valid_to'} for r in rows if r['valid_from'] <= t and (r['valid_to'] is None or r['valid_to'] > t)]
        self.assertEqual(at(earlier,4), at(e.segments,4))

    def test_invalid_and_out_of_order_fail(self):
        e = self.engine()
        e.observe(1,10,10,10)
        for values in [(1,10,10,10),(2,9,10,10),(2,float('nan'),10,10)]:
            with self.assertRaises(ValueError): e.observe(*values)

    def test_resource_limit_does_not_silently_drop(self):
        e = self.engine(max_segments=1)
        with self.assertRaises(RuntimeError):
            for t,p in enumerate([10,10.2,10.4,10.2,10,10.5],1): e.observe(t,p,p,p)

    def test_opening_spike_does_not_freeze_major_confirmation(self):
        e = SwingStructure()
        for t, (hi,lo,cl) in enumerate([(10,10,10),(10.2,10.2,10.2),
                                       (12,10.1,12),(12.1,12,12.1)],1):
            e.observe(t,hi,lo,cl)
        for t in range(5,16): e.observe(t,11.82,11.8,11.81)
        found = [s for s in e.segments if s['scale']=='major' and s['side']=='resistance' and s['price']==12.1]
        self.assertEqual(found, [])  # Lower volatility alone cannot confirm.
        self.assertAlmostEqual(e.detectors[1]['high'][2], .363)
        e.observe(16,11.7,11.7,11.7)
        found = [s for s in e.segments if s['scale']=='major' and s['side']=='resistance' and s['price']==12.1]
        self.assertEqual(found[0]['confirmed_at'],16)

    def test_no_confirmation_on_flat_or_recovering_close(self):
        e = SwingStructure()
        for t,p in enumerate([10,10.2,10.4],1): e.observe(t,p,p,p)
        threshold = e.detectors[1]['high'][2]
        for t in range(4,40): e.observe(t,10.35,10.35,10.35)
        self.assertEqual(e.detectors[1]['high'][2],threshold)
        self.assertFalse(any(s['price']==10.4 and s['reason']=='reversal_confirmed' for s in e.segments))

    def test_adjacent_touches_are_one_encounter_and_independent_retest_confirms(self):
        for sign in (1,-1):
            e = SwingStructure()
            transform = lambda p: 10+sign*(p-10)
            e.observe(1,10,10,10)
            for t in range(2,5):
                p=transform(10.3); c=transform(10.295)
                e.observe(t,max(p,c),min(p,c),c)
            self.assertFalse(any(s['reason'].startswith('boundary_') for s in e.segments))
            p=transform(10.22)
            e.observe(5,p,p,p)  # Entire bar away, not another touching candle.
            p,c=transform(10.3),transform(10.25)
            e.observe(6,max(p,c),min(p,c),c)
            found=[s for s in e.segments if s['reason']=='boundary_retest_confirmed']
            self.assertEqual(len(found),1)
            self.assertEqual(found[0]['side'],'resistance' if sign==1 else 'support')
            self.assertEqual(found[0]['confirmed_at'],6)
            self.assertIsNone(found[0]['reversal_distance'])

    def test_crossing_invalidates_stall_candidate(self):
        e=SwingStructure()
        for t,p in [(1,10),(2,10.3),(3,10.3),(4,10.5),(5,10.3)]: e.observe(t,p,p,p)
        self.assertFalse(any(s['price']==10.3 and s['reason'].startswith('boundary_') for s in e.segments))

    def test_sparse_tests_do_not_accumulate_across_long_gap(self):
        e=SwingStructure()
        for t,p in [(1,10),(2,10.3),(3,10.3),(140,10.3)]: e.observe(t,p,p,p)
        self.assertFalse(any(s['reason'].startswith('boundary_') for s in e.segments))

    def test_juns_three_failed_high_tests(self):
        e=SwingStructure()
        # Canonical JUNS 2026-08-21 07:13:48–58 ET, expressed as elapsed seconds.
        rows=[(6.02,5.79,6.02),(6.08,6.05,6.05),(6.08,6.05,6.077),
              (6.2,6.08,6.1999),(6.5,6.186,6.5),(6.57,6.39,6.4997),
              (6.49,6.4,6.472),(6.7,6.49,6.6),(6.88,6.6,6.6),
              (6.85,6.62,6.67),(6.8497,6.62,6.62)]
        for t,(hi,lo,cl) in enumerate(rows,48): e.observe(t,hi,lo,cl)
        found=[s for s in e.segments if s['price']==6.88 and s['scale']=='major']
        self.assertEqual((found[0]['confirmed_at'],found[0]['reason']),(58,'boundary_rejection_confirmed'))

    def test_quiet_stall_does_not_become_major_just_with_time(self):
        e=SwingStructure()
        e.observe(1,10,10,10)
        for t in range(2,100): e.observe(t,10.3,10.29,10.295)
        self.assertFalse(any(s['side']=='resistance' and s['scale']=='major' for s in e.segments))

    def test_established_range_suppresses_interior_but_not_breakout(self):
        e=SwingStructure()
        e._found({'scale':'major'},(10,0,.1),'support',1)
        e._found({'scale':'major'},(12,0,.1),'resistance',2)
        for t in range(3,22): e.observe(t,11,11,11)
        self.assertTrue(e._inside_consolidation(11.5,20,22))
        count=len(e.segments)
        e._found({'scale':'major'},(11.5,20,.1),'resistance',22)
        self.assertEqual(len(e.segments),count)
        self.assertEqual(e.counts['suppressed_interior_major'],1)
        e.observe(22,12.2,12.2,12.2)
        self.assertFalse(e._inside_consolidation(11.5,20,22))

    def test_repeated_contacts_do_not_duplicate_chart_segments(self):
        e = self.engine()
        l = self.seed_high(e)
        for t in range(5,105):
            if t%2: e.observe(t,10.4,10.3,10.35)
            else: e.observe(t,10.2,10.2,10.2)
        self.assertGreater(e.counts['events_rejection'],20)
        self.assertEqual(len([s for s in e.segments if s['level_id']==l['level_id']]),1)

    def test_contact_phase_preserves_pending_chart_segment(self):
        e = self.engine()
        l = self.seed_high(e)
        for t,p in [(5,10.5),(6,10.6)]: e.observe(t,p,p,p)
        count = len([s for s in e.segments if s['level_id']==l['level_id']])
        e.observe(7,10.45,10.4,10.45)
        self.assertEqual(l['state'],'retest_contact')
        self.assertEqual(len([s for s in e.segments if s['level_id']==l['level_id']]),count)
        self.assertEqual(e.segments[l['segment']]['state'],'pending')


class PreviewTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(service.router)
        self.client = TestClient(app)
        self.revision = dict(token='fixture', request_complete=True, complete_for_history=True)

    def page(self, request):
        self.assertFalse(request.include_structure)
        self.assertEqual(request.timeframe,'1s')
        self.assertEqual(request.authority,'history')
        end = datetime.fromisoformat(request.end)
        return SimpleNamespace(payload=dict(cache=dict(source_revision=self.revision),bars=[dict(
            bar_end=end.isoformat(),high=10.,low=10.,close=10.,event_count=1)]))

    def test_runnable_route_uses_bounded_canonical_bars(self):
        with patch.object(service,'qmd_product_request',side_effect=self.page) as read, patch.object(service,'qmd_historical_source_revision',return_value=self.revision):
            r = self.client.post('/api/research/swing-structure',json=dict(ticker='JUNS',session_date='2026-08-21'))
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(read.call_count,4)
        self.assertFalse(r.json()['persisted'])
        self.assertEqual(r.json()['counts']['bars'],4)

    def test_incomplete_source_and_changed_revision_fail(self):
        with patch.object(service,'qmd_product_request',return_value=SimpleNamespace(payload={'bars':[]})), patch.object(service,'qmd_historical_source_revision',return_value=self.revision):
            self.assertEqual(self.client.post('/api/research/swing-structure',json=dict(ticker='JUNS',session_date='2026-08-21')).status_code,422)
        with patch.object(service,'qmd_product_request',side_effect=self.page), patch.object(service,'qmd_historical_source_revision',side_effect=[self.revision,dict(self.revision,token='changed')]):
            self.assertEqual(self.client.post('/api/research/swing-structure',json=dict(ticker='JUNS',session_date='2026-08-21')).status_code,422)

    def test_busy_and_invalid_requests(self):
        service._busy.acquire()
        try:
            self.assertEqual(self.client.post('/api/research/swing-structure',json=dict(ticker='JUNS',session_date='2026-08-21')).status_code,429)
        finally: service._busy.release()
        self.assertEqual(self.client.post('/api/research/swing-structure',json=dict(ticker='JUNS',session_date='2026-08-21',reversal_bps=0)).status_code,422)


if __name__ == '__main__': unittest.main()
