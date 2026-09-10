from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

from src.trading_runtime import macd_hod as M, strategy_engine as S
from tests.test_structural_recovery import parameters as base_parameters, observation, level, NOW


def ready():
    p=base_parameters()
    p.pop('structural_recovery_contract');p.pop('structural_recovery')
    p.update(macd_hod_contract=M.CONTRACT,macd_hod=dict(M.DEFAULTS),liquidity_admission=dict(M.LIQUIDITY_181))
    p=S.resolve_long_momentum_parameters(p,revision=47)
    host=S.LongMomentumStrategyEngine(revision=47)
    a=S.StrategyAssignment('hod',S.STRATEGY_ID,47,'sim','TEST',123,S.AssignmentStatus.WATCHING,
                          S.StrategyPermissions(enter=True,reenter=True),p)
    o,_=observation(1,10.,10.1)
    rows=tuple(dict(level(-1,v,v+.02),selection_score=80) for v in [9.8,10.,10.4,10.8,11.5,12.5])
    o=replace(o,source_timeframe='100ms',bar_volume=1000.,structural_resistance_levels=rows,
              structural_session_high=10.5,macd_line=.01,macd_signal=0.,execution_vwap=9.7,
              bar_open=10.,bar_low=10.,bar_high=10.1)
    r=host.evaluate(a,o)
    a=replace(a,state=r.state,status=r.status)
    return host,a,step(o,.1)


def step(o,seconds,price=None,gap=None,**kw):
    price=o.price if price is None else price
    return replace(o,observed_at=o.observed_at+timedelta(seconds=seconds),price=price,
        bid=price-.005,ask=price+.005,bar_open=price-.01,bar_high=price+.01,bar_low=price-.01,
        macd_line=o.macd_line if gap is None else price*gap/10000,macd_signal=0.,**kw)


def test_entry_timeframe_frozen_references_and_mandatory_protection():
    host,a,o=ready();r=host.evaluate(a,o)
    assert r.evaluation.signals[0].action=='enter_long'
    intent,=r.evaluation.intents
    snapshot=intent.metadata['unified_structural_trigger']['current_snapshot']
    assert [x['upper'] for x in snapshot['levels']]==[10.42,10.02,9.82]
    assert snapshot['session_high']==10.5
    assert intent.invalidation_price<9.8
    assert intent.profit_target_price>o.ask
    assert intent.metadata['mandatory_broker_target']
    assert intent.resolved_execution_policy().envelope.deadline_ms==100
    assert len(intent.protection_profile.slices)==1
    assert S.strategy_rule_timeframes(a.parameters)>={'100ms','1s'}


def test_deadband_holds_but_more_than_ten_bps_exits():
    host,a,o=ready();r=host.evaluate(a,o)
    a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    held=host.evaluate(a,step(o,.1,gap=-10,position_quantity=100))
    assert held.evaluation.signals[0].action=='hold'
    assert held.state['macd_hod_state']['episode'] is not None
    a=replace(a,state=held.state,status=held.status)
    exit_=host.evaluate(a,step(o,.2,gap=-10.1,position_quantity=100))
    assert exit_.evaluation.intents[0].action=='exit'
    assert exit_.evaluation.intents[0].reason=='macd_bearish_gap_100ms'
    assert exit_.state['macd_hod_state']['episode'] is None
    from types import SimpleNamespace
    intent=exit_.evaluation.intents[0]
    assert intent.metadata['reentry_after_fill']
    assert S._fill_allows_reentry(a,SimpleNamespace(fill_role='managed_exit',
        reentry_after_fill=intent.metadata['reentry_after_fill']),exit_.state)


def test_only_closed_100ms_macd_can_end_episode():
    host,a,o=ready();r=host.evaluate(a,o);a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    for updates in [dict(source_timeframe='1s'),dict(evaluation_events=('market_data_update',))]:
        x=host.evaluate(a,step(o,.1,gap=-100,position_quantity=100,**updates))
        assert not x.evaluation.intents


def test_same_episode_reentry_uses_prior_high_and_waits_for_fills():
    host,a,o=ready();r=host.evaluate(a,o);a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    r=host.evaluate(a,step(o,.1,price=10.2,position_quantity=100));a=replace(a,state=r.state,status=r.status)
    waiting=host.evaluate(replace(a,status=S.AssignmentStatus.EXIT_PENDING),step(o,.2,position_quantity=100,pending_exit_quantity=100))
    assert waiting.evaluation.signals[0].reason=='exit_fill_pending'
    a=replace(a,status=S.AssignmentStatus.WATCHING)
    r=host.evaluate(a,step(o,.3,price=10.19));assert r.evaluation.signals[0].reason=='waiting_for_episode_high'
    a=replace(a,state=r.state,status=S.AssignmentStatus.WATCHING)
    r=host.evaluate(a,step(o,.4,price=10.23));assert r.evaluation.signals[0].action=='enter_long'


def test_future_levels_cannot_enter_and_quote_failure_does_not_disable_stop():
    host,a,o=ready()
    future=tuple(dict(r,confirmed_at_ms=o.observed_at.timestamp()*1000+1) for r in o.structural_resistance_levels)
    empty=deepcopy(a.state)
    empty['macd_hod_state']['rows']=[]
    assert not host.evaluate(replace(a,state=empty),replace(o,structural_resistance_levels=future)).evaluation.intents
    # A later snapshot cannot rewrite the already-known pre-trigger references.
    retained=host.evaluate(a,replace(o,structural_resistance_levels=future))
    assert retained.state['macd_hod_entry']['references']==host.evaluate(a,o).state['macd_hod_entry']['references']
    r=host.evaluate(a,o);a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    failed=host.evaluate(a,replace(step(o,.1,price=9.7,position_quantity=100),bid=0,ask=0,source_values={}))
    assert failed.evaluation.intents[0].action=='exit'


def test_stop_requires_confirmed_low_and_advances_without_moving_entry_references():
    host,a,o=ready();r=host.evaluate(a,o);a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    original=deepcopy(a.state['macd_hod_entry']['references'])
    state=deepcopy(a.state)
    state['macd_hod_state']['swing_low']={'price':10.05,'pivot_at':NOW.timestamp(),'confirmed_at':o.observed_at.timestamp()+.15}
    a=replace(a,state=state)
    r=host.evaluate(a,step(o,.1,price=10.5,position_quantity=100))
    assert not any(i.action=='replace_protective_stop' for i in r.evaluation.intents)
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,step(o,.2,price=10.5,position_quantity=100))
    assert r.evaluation.intents[0].action=='replace_protective_stop'
    assert r.state['active_stop']>a.state['active_stop']
    assert r.state['macd_hod_entry']['references']==original


def test_target_advances_after_break_and_rejection_restores_previous_price():
    import asyncio
    host,a,o=ready();r=host.evaluate(a,o)
    a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    prior=a.state['structural_profit_targets'][0]
    r=host.evaluate(a,replace(step(o,.1,price=10.9,position_quantity=100),bar_open=10.6))
    intent,=r.evaluation.intents
    assert intent.action=='replace_profit_target'
    assert intent.profit_target_price>prior
    assert intent.metadata['previous_profit_target']==prior
    assigned=S.AssignedLongMomentumStrategy([replace(a,state=r.state,status=r.status)])
    asyncio.run(assigned.on_intent_rejected(intent,reasons=('test_rejection',),event_time=o.observed_at))
    restored=assigned.assignments()[0]
    assert restored.state['structural_profit_targets']==[prior]
    retry=host.evaluate(restored,step(o,.2,price=10.9,position_quantity=100))
    assert retry.evaluation.intents[0].profit_target_price==intent.profit_target_price


def test_runtime_places_broker_stop_and_full_target(tmp_path):
    import asyncio
    from tests import test_long_momentum_strategy as T
    from src.trading_runtime.journal import TradingJournal
    async def run():
        _,a,o=ready()
        journal=TradingJournal(tmp_path/'journal.sqlite3')
        broker=T.SimulatedBrokerAdapter(['sim'],mode=T.TradingMode.BACKTEST)
        runtime=T.TradingRuntime(T.RunConfig(mode=T.RunMode.BACKTEST,strategy_id=S.STRATEGY_ID,
            strategy_revision=47,account_ids=('sim',),anchor_date=NOW.date(),run_id='macd-hod-test'),
            broker,S.AssignedLongMomentumStrategy([a]),journal,
            intent_planner=T.RuntimeIbkrStrategyOrderPlanner({'TEST':T.InstrumentContract('ibkr:123',123,'TEST','STK','USD')},
                strategy_id=S.STRATEGY_ID,strategy_revision=47))
        try:
            await runtime.initialize()
            stamp=o.observed_at-timedelta(milliseconds=1)
            await broker.on_market_event(T.QuoteEvent(ask_exchange=11,ask_price=o.ask,ask_size=10000,
                bid_exchange=12,bid_price=o.bid,bid_size=10000,conditions=(),indicators=(),
                ingest_ts=stamp,raw={'conid':123},sequence=1,source='test',tape=3,ticker='TEST',ts=stamp))
            await runtime.process_strategy_observation(o)
            orders=await broker.live_orders()
            assert len([r for r in orders if r.orderType=='STP'])==1
            assert len([r for r in orders if r.orderType=='LMT' and r.parentId])==1
        finally:
            journal.close()
    asyncio.run(run())


def test_entry_and_progression_survive_journal_chart_projection(tmp_path):
    from src.trading_runtime.journal import TradingJournal
    from src.backend.trading_runtime_service import strategy_activity_payload
    from src.backend.replay_run_service import _compact_strategy_chart_plan
    host,a,o=ready();r=host.evaluate(a,o)
    journal=TradingJournal(tmp_path/'chart.sqlite3')
    try:
        signal=r.evaluation.signals[0]
        journal.append(run_id='chart',category='strategy_decision',entity_type='signal',entity_id=signal.signal_id,
            event_time=o.observed_at,payload=dict(ticker=o.ticker,strategy_id=a.strategy_id,action='enter_long',
                reason=signal.reason,metadata=signal.metadata,invalidation_price=signal.invalidation_price))
        gate=strategy_activity_payload(journal=journal,run_id='chart')['rows'][0]['gate_snapshot']
        plan=_compact_strategy_chart_plan(gate)
        snap=plan['unified_structural_trigger']['current_snapshot']
        assert [r['entry_boundary'] for r in snap['levels']]==[10.42,10.02,9.82]
        assert snap['session_high']==10.5
        assert plan['decision_values']['initial_stop']>0
        assert len(plan['decision_values']['profit_targets'])==1
    finally:
        journal.close()
