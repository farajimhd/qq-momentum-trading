import pytest
from scripts.recover_strategy_audit import validate_result


def test_audit_recovery_refuses_an_unfinished_or_different_replay():
    case = dict(status='failed', run_id='run-1', symbol='TEST')
    result = dict(run=dict(status='completed', run_id='run-1', tickers=['TEST']))
    validate_result(case, result)
    for change in [dict(status='running'), dict(run_id='run-2'), dict(tickers=['OTHER'])]:
        with pytest.raises(ValueError):
            validate_result(case, dict(run=dict(result['run'], **change)))
    with pytest.raises(ValueError):
        validate_result(dict(case, status='active'), result)
