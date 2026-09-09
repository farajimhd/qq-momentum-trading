import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest
from src.backend.replay_run_service import (
    _checkpoint_has_strategy_observations, _strategy_observation_checkpoint,
    _strategy_observation_from_checkpoint)
from src.trading_runtime.strategy_engine import StrategyObservation


def test_checkpoint_preserves_causal_observation_before_next_completed_candle():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    original = StrategyObservation(ticker='TEST', observed_at=now, price=10.,
        evaluation_events=('market_data_update',), source_signal_ids=('trade:1',),
        structural_resistance_levels=({'lower':10., 'upper':10.2},),
        source_values={'indicator.macd.line@1s': {'observed_at':now.isoformat(), 'value':.1}},
        completed_range_context={'as_of':now.timestamp(), 'high':9.8})
    raw = json.loads(json.dumps(_strategy_observation_checkpoint(original)))
    assert _strategy_observation_from_checkpoint(raw, ticker='TEST', current_time=now) == original
    with pytest.raises(ValueError, match='future timestamp'):
        _strategy_observation_from_checkpoint(raw, ticker='OTHER', current_time=now)
    future = _strategy_observation_checkpoint(replace(original, observed_at=now+timedelta(seconds=1)))
    with pytest.raises(ValueError, match='future timestamp'):
        _strategy_observation_from_checkpoint(future, ticker='TEST', current_time=now)


def test_old_active_checkpoint_requires_explicit_observation_state():
    assert not _checkpoint_has_strategy_observations({'controller':{'strategy_engaged_tickers':['TEST']}})
    assert _checkpoint_has_strategy_observations({'controller':{
        'strategy_engaged_tickers':['TEST'], 'latest_strategy_observations':{}}})
    assert _checkpoint_has_strategy_observations({'controller':{'strategy_engaged_tickers':[]}})
