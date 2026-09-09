from datetime import datetime, timezone
from scripts.audit_strategy_positions import check_completed_range


def test_independent_range_audit_detects_omitted_pre_activation_high_and_future_context():
    def candle(at, high):
        return ({'bar_end': datetime.fromtimestamp(at, timezone.utc).isoformat(), 'high': high}, {})
    frames = [candle(101, 110), candle(110, 105), candle(111, 120)]
    context = dict(as_of=111., seconds=20., ready=True, coverage_start=100.,
                   window_start=100., high=110., samples=2)
    gate = dict(entry_confirmation={'at':111.}, entry_range_context=context,
                entry_range_high=110., entry_range_samples=2)
    assert check_completed_range(gate, frames, 100., 111.2)['status'] == 'passed'
    assert check_completed_range({}, frames, 100., 111.2)['status'] == 'not_recorded'
    context['high'] = gate['entry_range_high'] = 105.
    assert check_completed_range(gate, frames, 100., 111.2)['status'] == 'failed'
    context['as_of'] = 112.
    assert check_completed_range(gate, frames, 100., 111.2)['reason'] == 'range_confirmation_time_mismatch'
