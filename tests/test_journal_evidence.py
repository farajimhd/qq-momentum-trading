import json
from datetime import datetime, timezone
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.journal_evidence import REFERENCE


def test_evidence_roundtrip_compact_read_reopen_and_integrity(tmp_path):
    path = tmp_path / 'journal.sqlite3'
    journal = TradingJournal(path)
    payload = {'ticker': 'TEST', 'action': 'enter_long', 'metadata': {
        'profit_target': 12, 'unified_structural_trigger': {'levels': [
            {'price': n, 'evidence': 'original' * 100} for n in range(100)]}}}
    for _ in range(3):
        journal.append(run_id='run', category='strategy', entity_type='strategy_intent',
                       entity_id='entry', payload=payload, event_time=datetime.now(timezone.utc))
    stored = journal._fetchall('SELECT payload_json FROM journal')
    assert all(REFERENCE in row['payload_json'] for row in stored)
    assert journal._fetchone('SELECT count(*) n FROM journal_evidence')['n'] == 2
    assert journal.records('run')[0].payload['metadata'] == payload['metadata']
    compact = journal.strategy_activity_records(run_id='run', compact=True)
    assert compact[0].payload['metadata']['profit_target'] == 12
    assert len(json.dumps(compact[0].payload)) < 1000
    journal.close()
    journal = TradingJournal(path, read_only=True)
    assert journal.records('run')[0].payload['metadata'] == payload['metadata']
    journal.close()


def test_missing_evidence_fails_closed(tmp_path):
    import pytest
    journal = TradingJournal(tmp_path / 'journal.sqlite3')
    journal.append(run_id='run', category='strategy', entity_type='strategy_intent', entity_id='entry',
                   payload={'metadata': {'profit_target_selection': {'price': 12}}})
    with journal._connection:
        journal._connection.execute('DELETE FROM journal_evidence')
    with pytest.raises(ValueError, match='Missing or corrupt'):
        journal.records('run')
    journal.close()


def test_checkpoint_and_oms_state_recover_complete_evidence(tmp_path):
    journal = TradingJournal(tmp_path / 'recovery.sqlite3')
    state = {'profit_target_selection': {'references': [{'price': 12}], 'target': 12}}
    at = datetime.now(timezone.utc)
    journal.save_checkpoint('run', 'cursor', state, at)
    journal.save_order_management_state('group', run_id='run', account_id='sim', state=state)
    journal.save_portfolio_state('sim', state)
    assert journal.load_checkpoint('run')['state'] == state
    assert journal.order_management_states(run_id='run')[0]['state'] == state
    assert journal.portfolio_states()['sim'] == state
    journal.close()
