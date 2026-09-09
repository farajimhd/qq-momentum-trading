from scripts.measure_strategy_move_capture import capture, stamp


def test_move_capture_marks_partial_runner_and_preserves_actual_fees():
    executions = [dict(execution_id='buy',source_event_time='2026-01-01T00:00:01Z',
                      side='BUY',quantity='100',price='10',commission='1'),
                  dict(execution_id='partial',source_event_time='2026-01-01T00:00:02Z',
                      side='SELL',quantity='40',price='11',commission='1')]
    move = dict(entry_time=stamp('2026-01-01T00:00:00Z'),exit_time=stamp('2026-01-01T00:00:03Z'),
                entry_price=9.,exit_price=12.)
    result = capture(executions,move)
    assert result['quantity_at_peak'] == '60'
    assert result['marked_interval_pnl'] == '158.0'
    # Mark only the change during an interval that begins after acquisition.
    move.update(entry_time=stamp('2026-01-01T00:00:01.5Z'),entry_price=10.5)
    result = capture(executions,move)
    assert result['quantity_at_trough'] == '100'
    assert result['marked_interval_pnl'] == '109.0'
