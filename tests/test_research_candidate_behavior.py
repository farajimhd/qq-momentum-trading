from copy import deepcopy
import pytest
from scripts.create_swing_gap_candidate import patch_trading_behavior


def test_session_patch_preserves_direction_and_other_lifecycle_rules():
    profile = {'lifecycle':{'trading_behavior':{'side':'long', 'eligible_sessions':['premarket'],
        'entry_cutoff_time':'09:29:59', 'flatten_time':'09:29:59'}, 'initial_entry':{'unchanged':True}}}
    previous = deepcopy(profile)
    patch = {'eligible_sessions':['premarket','regular'], 'entry_cutoff_time':'15:45:00', 'flatten_time':'15:55:00'}
    patch_trading_behavior(profile, patch)
    assert profile['lifecycle']['initial_entry'] == previous['lifecycle']['initial_entry']
    assert profile['lifecycle']['trading_behavior']['side'] == 'long'
    patch['eligible_sessions'].append('after_hours')
    assert profile['lifecycle']['trading_behavior']['eligible_sessions'] == ['premarket','regular']
    with pytest.raises(ValueError, match='only'):
        patch_trading_behavior(profile, {'side':'short'})
    with pytest.raises(ValueError, match='must not follow'):
        patch_trading_behavior(profile, {'entry_cutoff_time':'16:00:00'})
