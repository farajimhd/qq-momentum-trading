from datetime import datetime, timezone
from types import SimpleNamespace
from src.trading_runtime.protection_timeline import attach_protection_timelines


def test_exact_entry_identity_separates_same_timestamp_reentry_and_pending_requests():
    at = '2026-09-08T08:00:00+00:00'
    lifecycles = [
        dict(lifecycle_id='closed', account_id='sim', side='LONG', closed_at=at, execution_ids=['e1']),
        dict(lifecycle_id='open', account_id='sim', side='LONG', closed_at=None, execution_ids=['e2']),
    ]
    executions = [SimpleNamespace(execution_id='e1', side='BUY', broker_order_id='1', client_order_id='entry-1'),
                  SimpleNamespace(execution_id='e2', side='BUY', broker_order_id='2', client_order_id='entry-2')]
    def event(seq, root, phase):
        return dict(sequence=seq, event_time=at, account_id='sim', entry_order_ids=[root],
                    phase=phase, kind='stop', order_id=root+'-stop', price=9, active=True)
    events = [event(1,'entry-1','effective'),event(2,'entry-2','requested'),event(3,'entry-2','effective')]
    assert attach_protection_timelines(lifecycles, events, executions, datetime.fromisoformat(at)) == 0
    assert [e['sequence'] for e in lifecycles[0]['protection_timeline']] == [1]
    assert [e['sequence'] for e in lifecycles[1]['protection_timeline']] == [2,3]
    assert lifecycles[1]['protection_timeline'][0]['phase'] == 'requested'


def test_future_and_unlinked_events_are_not_attached_by_ticker():
    rows = [dict(lifecycle_id='open', account_id='sim', side='LONG', closed_at=None, execution_ids=['e'])]
    executions = [SimpleNamespace(execution_id='e', side='BUY', broker_order_id='1', client_order_id='entry')]
    events = [dict(sequence=1, event_time='2026-09-08T09:00:00+00:00', account_id='sim', entry_order_ids=['entry']),
              dict(sequence=2, event_time='2026-09-08T08:00:00+00:00', account_id='sim', entry_order_ids=['unrelated'])]
    assert attach_protection_timelines(rows, events, executions, datetime(2026,9,8,8,30,tzinfo=timezone.utc)) == 1
    assert rows[0]['protection_timeline'] == []
