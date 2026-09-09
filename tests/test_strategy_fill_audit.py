import pytest

from scripts.audit_strategy_positions import entry_fill_location, linked_entry


def test_entry_link_uses_intent_identity_even_when_decision_times_match():
    first = dict(signal_id='first', event_time='2026-01-01T00:00:01+00:00')
    second = dict(signal_id='second', event_time=first['event_time'])
    position = dict(protection_timeline=[dict(source_intent_id='second'),
                                        dict(source_intent_id='second'),
                                        dict(source_intent_id='later-stop-change')])
    assert linked_entry(position, [first, second]) is second


def test_entry_link_rejects_missing_or_ambiguous_lifecycle_evidence():
    entries = [dict(signal_id='first'), dict(signal_id='second')]
    for timeline in ([], [dict(source_intent_id='unknown')],
                     [dict(source_intent_id='first'), dict(source_intent_id='second')]):
        with pytest.raises(ValueError, match='exactly one identity-linked'):
            linked_entry(dict(protection_timeline=timeline), entries)


def test_fill_location_uses_lifecycle_identity_and_separates_later_acquisition():
    def fill(key, second, price, quantity, side='BUY'):
        return dict(execution_id=key, source_event_time=f'2026-01-01T00:00:{second:02d}+00:00',
                    price=price, quantity=quantity, side=side)

    executions = {row['execution_id']: row for row in (
        fill('first', 1, '10.02', '10'), fill('later', 2, '10.00', '5'),
        fill('exit', 3, '9', '15', 'SELL'), fill('unrelated', 0, '8', '100'))}
    position = dict(execution_ids=['exit', 'later', 'first'])
    result = entry_fill_location(position, executions, 10.00)
    assert result['first_execution_id'] == 'first'
    assert result['first_fill_above_threshold'] is True
    assert result['acquired_quantity'] == '15'
    assert result['quantity_at_or_below_threshold'] == '5'
    assert entry_fill_location(position, executions, 10.02)['first_fill_above_threshold'] is False
    assert entry_fill_location(position, executions, None)['first_fill_above_threshold'] is None


def test_fill_location_fails_closed_for_missing_execution_evidence():
    with pytest.raises(KeyError):
        entry_fill_location(dict(execution_ids=['missing']), {}, 10)
    with pytest.raises(ValueError, match='no linked acquisition'):
        entry_fill_location(dict(execution_ids=[]), {}, 10)
