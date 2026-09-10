from dataclasses import replace
from datetime import timedelta
import asyncio
import pytest

from tests.test_macd_threshold import fixture as base_fixture, NOW
from src.trading_runtime import macd_r3 as M, strategy_engine as S


def level(price, at=None):
    return dict(lower=price, upper=price, side=-1, book_version=M.BOOK_VERSION,
                confirmed_at_ms=(at or NOW-timedelta(seconds=1)).timestamp()*1000,
                unified_level_id=str(price))


def fixture():
    a, o = base_fixture()
    p = S.resolve_long_momentum_parameters(dict(macd_r3_contract=M.CONTRACT,
        reentry=dict(enabled=True, after_protective_exit=True)), revision=47)
    return replace(a, parameters=p), replace(o, execution_vwap=9., structural_session_high=10.5,
        structural_resistance_levels=tuple(level(p) for p in [9.5, 10., 10.5, 11.]))


def test_initial_entry_has_ranked_references_and_broker_protection():
    a, o = fixture()
    r = M.evaluate(a, o)
    i, = r.evaluation.intents
    assert i.action == 'enter_long' and i.quantity == 100
    assert i.invalidation_price == 9.49 and i.profit_target_price == 11.
    assert i.metadata['initial_stop'] == 9.49
    assert [x['upper'] for x in i.metadata['current_resistance_snapshot']['levels']] == [10.5, 10., 9.5]
    assert i.resolved_protection_profile() is not None


def test_entry_chart_projection_retains_levels_and_protection():
    from src.backend.replay_run_service import _compact_strategy_chart_plan
    a, o = fixture()
    metadata = M.evaluate(a, o).evaluation.signals[0].metadata
    chart = _compact_strategy_chart_plan(metadata)
    assert len(chart['unified_structural_trigger']['current_snapshot']['levels']) == 3
    assert chart['decision_values']['initial_stop'] == 9.49
    assert chart['decision_values']['profit_target'] == 11.


@pytest.mark.parametrize('change', [dict(execution_vwap=10.), dict(source_values={}),
    dict(price=9.5), dict(macd_line=-.005), dict(source_timeframe='1s'),
    dict(structural_resistance_levels=()),
    dict(structural_resistance_levels=tuple(level(p) for p in [9.5,10.,10.5]))])
def test_entry_requires_every_condition(change):
    a, o = fixture()
    assert not M.evaluate(a, replace(o, **change)).evaluation.intents


def test_future_levels_and_duplicates_cannot_create_references():
    a, o = fixture()
    o = replace(o, structural_resistance_levels=(level(9.5),level(9.5),level(10.),
        level(10.5, NOW+timedelta(milliseconds=1)),level(11.)))
    assert len(M.references(o)[0]) == 2
    assert not M.evaluate(a, o).evaluation.intents


def test_management_holds_through_negative_macd_and_advances_only_upward():
    a, o = fixture()
    r = M.evaluate(a, o)
    a = replace(a, state=r.state, status=S.AssignmentStatus.MANAGING)
    o = replace(o, observed_at=NOW+timedelta(milliseconds=100), position_quantity=100,
                macd_line=-.02, execution_vwap=12., source_values={})
    assert M.evaluate(a,o).evaluation.signals[0].action == 'hold'
    moved = replace(o, price=10.3, bid=10.29, ask=10.31, structural_session_high=10.8,
        structural_resistance_levels=tuple(level(p) for p in [9.8,10.3,10.8,11.5]))
    stop = M.evaluate(a,moved)
    assert stop.evaluation.intents[0].action == 'replace_protective_stop'
    assert stop.evaluation.intents[0].invalidation_price == 9.79
    a = replace(a,state=stop.state)
    target = M.evaluate(a,replace(moved,observed_at=NOW+timedelta(milliseconds=200)))
    assert target.evaluation.intents[0].action == 'replace_profit_target'
    assert target.evaluation.intents[0].profit_target_price == 11.5
    a = replace(a,state=target.state)
    lowered = M.evaluate(a,replace(o,observed_at=NOW+timedelta(milliseconds=300)))
    assert lowered.state['active_stop'] == 9.79 and lowered.state['r3_target'] == 11.5
    reached = M.evaluate(a,replace(moved,price=11.5,observed_at=NOW+timedelta(milliseconds=400)))
    assert reached.evaluation.intents[0].action == 'exit'


@pytest.mark.parametrize('gap,action',[(-4.,'wait'),(0.,'wait'),(.1,'enter_long')])
def test_reentry_requires_positive_macd(gap,action):
    a,o = fixture()
    a = replace(a,state={'r3_ever_filled':True})
    assert M.evaluate(a,replace(o,macd_line=gap/1000)).evaluation.signals[0].action == action


def test_runtime_bracket_stop_fill_then_reentry(tmp_path):
    from tests import test_long_momentum_strategy as T
    from src.trading_runtime.journal import TradingJournal
    async def run():
        a,o = fixture()
        broker = T.SimulatedBrokerAdapter(['sim'],mode=T.TradingMode.BACKTEST)
        journal = TradingJournal(tmp_path/'r3.sqlite3')
        strategy = S.AssignedLongMomentumStrategy([a])
        runtime = T.TradingRuntime(T.RunConfig(mode=T.RunMode.BACKTEST,strategy_id=S.STRATEGY_ID,
            strategy_revision=47,account_ids=('sim',),anchor_date=NOW.date(),run_id='r3'),broker,strategy,journal,
            intent_planner=T.RuntimeIbkrStrategyOrderPlanner({'TEST':T.InstrumentContract('ibkr:123',123,'TEST','STK','USD')},
                strategy_id=S.STRATEGY_ID,strategy_revision=47))
        async def tick(ms, price):
            at=NOW+timedelta(milliseconds=ms)
            await runtime.process_event(T.QuoteEvent(ask_exchange=11,ask_price=price+.01,ask_size=10000,
                bid_exchange=12,bid_price=price-.01,bid_size=10000,conditions=(),indicators=(),ingest_ts=at,
                raw={'conid':123},sequence=ms+1,source='test',tape=3,ticker='TEST',ts=at),evaluate_strategy=False)
        try:
            account=runtime.portfolio.states['sim']
            account.profile=replace(account.profile,policy=replace(account.profile.policy,allow_outside_rth=True))
            await runtime.initialize()
            await tick(0,10.)
            await runtime.process_strategy_observation(o)
            orders=await broker.live_orders()
            assert len(orders)==3
            assert sorted(x.orderType for x in orders)==['LMT','LMT','STP']
            await tick(10,10.)
            assert strategy.assignments()[0].status==S.AssignmentStatus.MANAGING
            advanced=replace(o,observed_at=NOW+timedelta(milliseconds=50),position_quantity=100,
                structural_resistance_levels=tuple(level(p) for p in [9.6,10.1,10.5,11.5]))
            await runtime.process_strategy_observation(advanced)
            stops=[x for x in await broker.live_orders() if x.orderType=='STP']
            assert len(stops)==1 and stops[0].auxPrice==9.59
            await runtime.process_strategy_observation(replace(advanced,observed_at=NOW+timedelta(milliseconds=60)))
            targets=[x for x in await broker.live_orders() if x.orderType=='LMT' and x.parentId]
            assert len(targets)==1 and targets[0].price==11.5
            await tick(100,9.4)
            assert all(float(p.position)==0 for p in await broker.positions('sim'))
            assert strategy.assignments()[0].status==S.AssignmentStatus.REENTRY_COOLDOWN
            await tick(200,10.)
            await runtime.process_strategy_observation(replace(o,observed_at=NOW+timedelta(milliseconds=200),macd_line=.001))
            await tick(210,10.)
            assert strategy.assignments()[0].status==S.AssignmentStatus.MANAGING
            await tick(300,11.1)
            assert all(float(p.position)==0 for p in await broker.positions('sim'))
            assert strategy.assignments()[0].status==S.AssignmentStatus.REENTRY_COOLDOWN
        finally:
            journal.close()
    asyncio.run(run())
