"""Build an independent historical candidate through configuration authority."""
from copy import deepcopy

from src.trading_runtime.structural_recovery import CONTRACT, DEFAULTS, LIQUIDITY, LIQUIDITY_181

PROFILE_ID = 'v6-structural-support-recovery'
LABEL = 'V6 structural support recovery'


def build(base, *, align_179=False):
    liquidity = LIQUIDITY_181 if align_179 else LIQUIDITY
    label = LABEL + (' - 179 gates' if align_179 else '')
    payload = deepcopy(base)
    canvas = payload.pop('canvas')
    # The balanced profile supplies configuration schema, not trading rules.
    template = next(p for p in payload['strategy']['profiles'] if p['profile_id']=='long-momentum-balanced')
    profile = deepcopy(template)
    profile.update(profile_id=PROFILE_ID, name=label, revision=1, derived_from_profile_id='',
        origin='user', editable=True, protected=False, publication_status='draft',
        description='Independent V6 support recovery. Completed 1s structural confirmation, mandatory fresh liquidity/spread/volume gates, structural stop and next-resistance full target. No MACD gate. Backtest only; select a certified V6 book.')
    profile['action_policy_ids'] = []
    from src.trading_runtime.strategy_engine import default_long_momentum_parameters
    parameters = default_long_momentum_parameters(revision=profile['definition_revision'])
    parameters.update(structural_recovery_contract=CONTRACT, structural_recovery=dict(DEFAULTS),
        structural_detector_settings={}, liquidity_admission=dict(liquidity),
        require_open_macd_for_entry=False,require_positive_macd_signal_for_entry=False)
    parameters['sizing'].update(request_mode='risk_fraction',request_value=DEFAULTS['risk_fraction'])
    parameters['reentry'].update(enabled=True,cooldown_ms=0,unlimited_attempts=True,
        maximum_attempts=0,require_new_confirmation=False)
    profile['parameters'] = parameters
    lifecycle = profile['lifecycle']
    lifecycle['phase_modes'] = {k:'automatic' for k in ('initial_entry','manage','exit','reentry')}
    capital = dict(mode='risk_fraction',value=DEFAULTS['risk_fraction'],maximum_quantity=DEFAULTS['maximum_quantity'],allow_replacement=False)
    order = dict(execution_policy='adaptive_urgent',deadline_ms=1000,
        partial_fill_policy='complete_remainder',protection_profile='structural-single-target')
    # A source subscription only. The independent executor owns all decisions.
    rule_id = PROFILE_ID+'-observe'
    payload['market_discovery']['rule_sets'].append(dict(rule_set_id=rule_id,name='Observe completed price',
        enabled=True,operator='all',conditions=[dict(condition_id='price-observed',enabled=True,
            left_source_id='market.last_price',left_timeframe='1s',comparator='greater_than',value=0.)]))
    invalid_id=PROFILE_ID+'-invalid-price'
    payload['market_discovery']['rule_sets'].append(dict(rule_set_id=invalid_id,name='Invalid market price',
        enabled=True,operator='all',conditions=[dict(condition_id='invalid-price',enabled=True,
            left_source_id='market.last_price',left_timeframe='1s',comparator='less_or_equal',value=0.)]))
    stage = dict(expression={'kind':'rule_set','rule_set_id':rule_id},groups=[],operator='all')
    blocker = dict(expression={'kind':'rule_set','rule_set_id':invalid_id},groups=[],operator='any')
    parameters['entry_rules']={name:dict(operator='all',rule_sets=[deepcopy(next(
        r for r in payload['market_discovery']['rule_sets'] if r['rule_set_id']==identifier))])
        for name,identifier in [('trigger',rule_id),('confirmation',rule_id),('veto',invalid_id)]}
    lifecycle['initial_entry'] = dict(action_id='position.enter_long',capital_request=capital,
        order_intent=order,opportunity=deepcopy(stage),confirmation=deepcopy(stage),
        blockers=deepcopy(blocker),add_steps=[])
    lifecycle['reentry'] = dict(action_id='position.enter_long',enabled=True,cooldown_ms=0,
        unlimited_attempts=True,maximum_attempts=0,after_protective_exit=True,require_new_confirmation=False,
        capital_request=deepcopy(capital),order_intent=deepcopy(order),
        rules=dict(opportunity=deepcopy(stage),confirmation=deepcopy(stage),blockers=deepcopy(blocker)))
    lifecycle['exit'] = {'rule_sets':[]}
    lifecycle['trading_behavior'] = dict(side='long',eligible_sessions=['premarket','regular'],
        entry_cutoff_time='15:45:00',flatten_time='15:55:00')
    payload['strategy']['profiles'].append(profile)
    payload['strategy']['active_profile_id'] = PROFILE_ID
    source = next(p for p in payload['run_plans']['plans'] if p['run_plan_id']=='balanced-replay')
    plan = deepcopy(source)
    plan_id = PROFILE_ID+'-backtest'
    plan.update(run_plan_id=plan_id,profile_id=PROFILE_ID,name=label,description=profile['description'],
        compiled=False,allowed_environments=['backtest'],signal_stream_ids=[],watchlist_ids=[])
    quality_id=PROFILE_ID+'-tradability'
    conditions=[]
    for index,(field,comparator,threshold) in enumerate([
        ('market.last_price','greater_or_equal',liquidity['minimum_price']),
        ('market.last_price','less_or_equal',liquidity['maximum_price']),
        ('market.session_dollar_volume','greater_or_equal',liquidity['minimum_session_dollar_volume']),
        ('market.volume','greater_or_equal',liquidity['minimum_session_share_volume']),
        ('market.trade_rate_10s','greater_or_equal',liquidity['minimum_trade_rate_10s']),
        ('market.trade_rate_60s','greater_or_equal',liquidity['minimum_trade_rate_60s']),
        ('market.spread_bps','less_or_equal',liquidity['maximum_admission_spread_bps'])]):
        conditions.append(dict(condition_id=f'tradability-{index}',enabled=True,left_source_id=field,
            left_field_ref=f'data.{field}@1:value',right_source_id='',comparator=comparator,value=threshold))
    payload['market_discovery']['rule_sets'].append(dict(rule_set_id=quality_id,name='V6 tradability',
        description='Price, absolute session volume, sustained trade rates and spread; rechecked before acquisition.',
        enabled=True,operator='all',conditions=conditions,scope='watchlist',origin='user',editable=True,
        protected=False,revision=1,implementation_status='implemented',publication_status='draft'))
    watch=deepcopy(next(w for w in payload['market_discovery']['watchlists'] if w['watchlist_id']=='squeeze-tradable-candidates'))
    watch.update(watchlist_id=quality_id,name='V6 tradable tickers',description='Tradability only; no squeeze or MACD prerequisite.',
        origin='user',template=False,inclusion_rule_sets=[quality_id],maximum_size=10000)
    payload['market_discovery']['watchlists'].append(watch)
    stream=deepcopy(next(s for s in payload['market_discovery']['signal_streams'] if s['signal_stream_id']=='price-squeeze-early'))
    stream.update(signal_stream_id=quality_id,name='V6 tradability admitted',origin='user',protected=False,
        description='Activate observation when absolute tradability gates pass; the strategy independently confirms support recovery.',
        occurrence_source=None,episode_role=None,episode_ttl_ms=None,inclusion_rule_sets=[quality_id],
        refresh_interval_ms=1000,columns=['symbol','last_price','volume','spread_bps'],
        column_intervals={},column_aggregations={})
    payload['market_discovery']['signal_streams'].append(stream)
    universe=deepcopy(next(u for u in payload['run_plans']['universes'] if u['universe_id']==source['universe_id']))
    universe.update(universe_id=PROFILE_ID+'-universe',name='V6 tradability universe',
        scanner_view_id=quality_id,scanner_view_ids=[quality_id],signal_stream_ids=[quality_id],
        signal_stream_snapshots=[deepcopy(stream)],watchlist_snapshots=[deepcopy(watch)])
    payload['run_plans']['universes'].append(universe)
    plan.update(universe_id=universe['universe_id'],signal_stream_ids=[quality_id],watchlist_ids=[quality_id])
    for key in ('runtime_assignments','observation_dependencies'):
        plan.pop(key,None)
    mandates = []
    for m in list(payload['portfolio']['mandates']):
        if m['mandate_id'] in source['mandate_ids']:
            clone=deepcopy(m)
            clone.update(mandate_id=plan_id+'-'+m['mandate_id'],run_plan_id=plan_id)
            clone['principal_id']=str(m['principal_id']).replace(source['run_plan_id'],plan_id)
            clone['maximum_planned_risk_fraction']=min(float(m.get('maximum_planned_risk_fraction') or .01),DEFAULTS['risk_fraction'])
            payload['portfolio']['mandates'].append(clone)
            mandates.append(clone['mandate_id'])
    plan['mandate_ids']=mandates
    payload['run_plans']['plans'].append(plan)
    return payload,canvas,plan_id


def create(expected_revision=181):
    if expected_revision not in (180,181):
        raise ValueError('Only the original 180 and revised 181 candidates are supported')
    label = LABEL + (' - 179 gates' if expected_revision == 181 else '')
    from src.backend.trading_configuration_service import configuration_base, create_test_candidate
    from src.backend.trading_runtime_service import trading_journal
    existing=trading_journal().trading_configuration_candidate_summaries()
    match=next((c for c in existing if c['candidate_revision']==expected_revision),None)
    if match:
        if match['label'] != label:
            raise ValueError(f'Candidate {expected_revision} is already occupied; refusing overwrite')
        return match
    if (max((c['candidate_revision'] for c in existing),default=0)+1) != expected_revision:
        raise ValueError('Next candidate number differs; refusing to create a different revision')
    payload,canvas,plan_id=build(configuration_base(), align_179=expected_revision == 181)
    result=create_test_candidate(label=label,canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan_id,strategy_profile_id=PROFILE_ID)
    if result['candidate_revision'] != expected_revision:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
