"""Attach OMS protection events through exact entry-order lifecycle identity."""
from datetime import datetime


def attach_protection_timelines(lifecycles, events, executions, as_of):
    execution_by_id = {str(row.execution_id): row for row in executions}
    roots = {}
    for lifecycle in lifecycles:
        ids = set()
        opening_side = 'SELL' if lifecycle.get('side') == 'SHORT' else 'BUY'
        for execution_id in lifecycle.get('execution_ids', []):
            execution = execution_by_id.get(str(execution_id))
            if execution and execution.side.upper() == opening_side:
                ids.update(str(v) for v in (execution.broker_order_id, execution.client_order_id) if v)
        for order_id in ids:
            roots.setdefault((lifecycle['account_id'], order_id), []).append(lifecycle)
        lifecycle['protection_timeline'] = []
    unmatched = 0
    for event in sorted(events, key=lambda e: (datetime.fromisoformat(e['event_time']), e['sequence'])):
        if datetime.fromisoformat(event['event_time']) > as_of:
            continue
        matches = {row['lifecycle_id']: row for root in event['entry_order_ids']
                   for row in roots.get((event['account_id'], root), [])}
        # A reused entry order may open more than one position. The exact order
        # relation is primary; causal boundaries disambiguate only those matches.
        eligible = [row for row in matches.values()
                    if not row.get('closed_at') or datetime.fromisoformat(event['event_time']) <= datetime.fromisoformat(row['closed_at'])]
        if len(eligible) != 1:
            unmatched += 1
            continue
        row = eligible[0]
        row['protection_timeline'].append({**event, 'lifecycle_id': row['lifecycle_id']})
    return unmatched
