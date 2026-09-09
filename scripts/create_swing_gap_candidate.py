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
import math
from datetime import time as clock_time


def patch_trading_behavior(profile, patch):
    allowed = {'eligible_sessions', 'entry_cutoff_time', 'flatten_time'}
    if not isinstance(patch, dict) or not patch or set(patch)-allowed:
        raise ValueError('Trading behavior patch accepts sessions and session cutoff times only')
    behavior = deepcopy(profile['lifecycle']['trading_behavior'])
    behavior.update(deepcopy(patch))
    sessions = behavior.get('eligible_sessions')
    if (not isinstance(sessions, list) or not sessions
            or any(s not in ('premarket', 'regular', 'after_hours') for s in sessions)
            or len(set(sessions)) != len(sessions)):
        raise ValueError('Specify unique supported trading sessions')
    times = {}
    for key in ('entry_cutoff_time', 'flatten_time'):
        if behavior.get(key):
            times[key] = clock_time.fromisoformat(behavior[key])
            if times[key].tzinfo is not None:
                raise ValueError('Session times use the existing exchange timezone')
    if len(times) == 2 and times['entry_cutoff_time'] > times['flatten_time']:
        raise ValueError('Entry cutoff must not follow flatten time')
    profile['lifecycle']['trading_behavior'] = behavior


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-candidate', required=True)
    parser.add_argument('--base-profile', default='swing-v4-momentum-v1')
    parser.add_argument('--profile-id')
    parser.add_argument('--label', default='Swing v4 gaps v3 - research')
    parser.add_argument('--description', help='Describe the isolated strategy contract')
    parser.add_argument('--preserve-parameters', action='store_true')
    parser.add_argument('--parameters-json', type=Path, help='Explicit incremental parameter patch; preserve the source parameters first')
    parser.add_argument('--trading-behavior-json', type=Path,
        help='Explicit cloned-profile session and cutoff patch; does not alter published profiles')
    parser.add_argument('--mandate-risk-fraction', type=float,
        help='Lower the cloned mandates maximum planned risk fraction; never increases the source limit')
    args = parser.parse_args()
    if args.mandate_risk_fraction is not None and (
            not math.isfinite(args.mandate_risk_fraction) or not 0 < args.mandate_risk_fraction <= 1):
        parser.error('--mandate-risk-fraction must be finite and in (0, 1]')
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
    profile_id = args.profile_id or CONTRACT
    plan_id = profile_id + '-replay'
    mandate_ids = []
    for source in base['payload']['portfolio']['mandates']:
        if source['mandate_id'] in plan.get('mandate_ids', []):
            mandate = deepcopy(source)
            mandate['mandate_id'] = source['mandate_id'].replace(plan['run_plan_id'], plan_id)
            mandate['principal_id'] = source['principal_id'].replace(plan['run_plan_id'], plan_id)
            mandate['run_plan_id'] = plan_id
            if args.mandate_risk_fraction is not None:
                previous = mandate.get('maximum_planned_risk_fraction')
                if previous is None or args.mandate_risk_fraction > float(previous):
                    parser.error('--mandate-risk-fraction requires an explicit source limit at least as large')
                mandate['maximum_planned_risk_fraction'] = args.mandate_risk_fraction
            mandate_ids.append(mandate['mandate_id'])
            payload['portfolio']['mandates'] = [m for m in payload['portfolio']['mandates'] if m['mandate_id'] != mandate['mandate_id']] + [mandate]
    plan['mandate_ids'] = mandate_ids
    if args.mandate_risk_fraction is not None and not mandate_ids:
        parser.error('The source plan has no mandates to constrain')
    universe = deepcopy(next(u for u in base['payload']['run_plans']['universes'] if u['universe_id'] == plan['universe_id']))
    universe['universe_id'] = 'run-plan-' + plan_id + '-candidates'
    payload['run_plans']['universes'] = [u for u in payload['run_plans']['universes'] if u['universe_id'] != universe['universe_id']] + [universe]
    plan['universe_id'] = universe['universe_id']
    profile.update(profile_id=profile_id, name=args.label,
        derived_from_profile_id=args.base_profile, publication_status='draft', editable=True,
        description='Research: confirmed cluster breakout or support bounce, executable-price risk ceiling, mature support at least $0.10 below entry, partial target and support-protected runner. Failed support requires reclaim.')
    parameters = profile['parameters']
    if not args.preserve_parameters:
        parameters.pop('swing_momentum_contract', None)
        parameters.pop('swing_momentum', None)
        parameters.update(swing_gap_contract=CONTRACT, swing_gap=dict(DEFAULTS))
        from src.trading_runtime.gap_continuation import DEFAULTS as CONTINUATION_DEFAULTS
        parameters['gap_continuation'] = dict(CONTINUATION_DEFAULTS)
        parameters['protection']['stop'].update(method='structure', cap_initial_stop_distance=False)
        parameters['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=True)
        parameters['momentum_management']['macd_backstop']['enabled'] = False
    else:
        profile['description'] = 'Research candidate cloned from '+args.base_profile+'; incremental changes are recorded in the experiment ledger.'
    if args.parameters_json:
        if not args.preserve_parameters:
            parser.error('--parameters-json requires --preserve-parameters')
        def update(target, patch):
            for key, value in patch.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    update(target[key], value)
                else:
                    target[key] = value
        update(parameters, json.loads(args.parameters_json.read_text(encoding='utf-8')))
    if args.description:
        profile['description'] = args.description
    if args.trading_behavior_json:
        patch_trading_behavior(profile, json.loads(args.trading_behavior_json.read_text(encoding='utf-8')))
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
