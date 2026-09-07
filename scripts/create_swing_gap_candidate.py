"""Create an isolated backtest candidate using the normal configuration authority."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from copy import deepcopy
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-candidate', required=True)
    parser.add_argument('--base-profile', default='swing-v4-momentum-v1')
    args = parser.parse_args()
    from src.backend.trading_configuration_service import configuration_candidate, create_test_candidate, configuration_base
    from src.trading_runtime.swing_gap import CONTRACT, DEFAULTS
    base = configuration_candidate(args.base_candidate, required=True)
    payload = deepcopy(base['payload'])
    profile = deepcopy(next(p for p in payload['strategy']['profiles'] if p['profile_id'] == args.base_profile))
    plan = deepcopy(next(p for p in payload['run_plans']['plans'] if p['profile_id'] == args.base_profile))
    canvas = payload.pop('canvas')
    payload = deepcopy(configuration_base())
    rules = payload['market_discovery']['rule_sets']
    for rule in base['payload']['market_discovery']['rule_sets']:
        if rule['rule_set_id'] in ('evidence-macd-open-1s', 'evidence-macd-closed'):
            existing = next((r for r in rules if r['rule_set_id'] == rule['rule_set_id']), None)
            if existing is None:
                rules.append(deepcopy(rule))
            elif existing['conditions'] != rule['conditions']:
                raise ValueError('Existing MACD rule differs from the source candidate')
    profile_id = 'swing-v4-gap-v1'
    plan_id = profile_id + '-replay'
    mandate_ids = []
    for source in base['payload']['portfolio']['mandates']:
        if source['mandate_id'] in plan.get('mandate_ids', []):
            mandate = deepcopy(source)
            mandate['mandate_id'] = source['mandate_id'].replace(plan['run_plan_id'], plan_id)
            mandate['principal_id'] = source['principal_id'].replace(plan['run_plan_id'], plan_id)
            mandate['run_plan_id'] = plan_id
            mandate_ids.append(mandate['mandate_id'])
            payload['portfolio']['mandates'] = [m for m in payload['portfolio']['mandates'] if m['mandate_id'] != mandate['mandate_id']] + [mandate]
    plan['mandate_ids'] = mandate_ids
    universe = deepcopy(next(u for u in base['payload']['run_plans']['universes'] if u['universe_id'] == plan['universe_id']))
    universe['universe_id'] = 'run-plan-' + plan_id + '-candidates'
    payload['run_plans']['universes'] = [u for u in payload['run_plans']['universes'] if u['universe_id'] != universe['universe_id']] + [universe]
    plan['universe_id'] = universe['universe_id']
    profile.update(profile_id=profile_id, name='Swing v4 gaps - research',
        derived_from_profile_id=args.base_profile, publication_status='draft', editable=True,
        description='Research: causal v4 gaps, MACD-open and VWAP/liquidity entry; fixed structural support at least $0.10 below entry and fixed target below the upper gap resistance. No MACD-close exit.')
    parameters = profile['parameters']
    parameters.pop('swing_momentum_contract', None)
    parameters.pop('swing_momentum', None)
    parameters.update(swing_gap_contract=CONTRACT, swing_gap=dict(DEFAULTS))
    parameters['protection']['stop'].update(method='structure', cap_initial_stop_distance=False)
    parameters['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=True)
    parameters['momentum_management']['macd_backstop']['enabled'] = False
    plan.update(run_plan_id=plan_id, profile_id=profile_id, name=profile['name'],
        description=profile['description'], compiled=False,
        allowed_environments=['replay', 'backtest', 'backtest_debug'])
    for key in ('runtime_assignments', 'observation_dependencies'):
        plan.pop(key, None)
    payload['strategy']['profiles'] = [p for p in payload['strategy']['profiles'] if p['profile_id'] != profile_id] + [profile]
    payload['run_plans']['plans'] = [p for p in payload['run_plans']['plans'] if p['run_plan_id'] != plan_id] + [plan]
    print('Creating one research-only gap candidate; published strategies remain unchanged.', file=sys.stderr, flush=True)
    result = create_test_candidate(label=profile['name'], canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'], configuration=payload,
        run_plan_id=plan_id, strategy_profile_id=profile_id)
    print(json.dumps({k: result[k] for k in ('candidate_id', 'candidate_revision', 'label', 'content_hash')}))


if __name__ == '__main__':
    main()
