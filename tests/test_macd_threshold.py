from dataclasses import replace
from datetime import datetime,timedelta,timezone
import asyncio
import pytest

from src.trading_runtime import macd_threshold as M,strategy_engine as S

NOW=datetime(2026,8,21,8,10,tzinfo=timezone.utc)


def fixture(gap=-4.,quantity=0):
    p=S.resolve_long_momentum_parameters({'macd_threshold_contract':M.CONTRACT,'macd_threshold':dict(M.DEFAULTS)},revision=47)
    a=S.StrategyAssignment('threshold',S.STRATEGY_ID,47,'sim','TEST',123,S.AssignmentStatus.WATCHING,
        S.StrategyPermissions(enter=True,reenter=True),p)
    values={k:dict(value=v,observed_at=NOW.isoformat()) for k,v in {
        'market.session_dollar_volume':2e6,'market.volume':200000,'market.trade_rate_10s':20,
        'market.trade_rate_60s':10,'market.spread_bps':20}.items()}
    o=S.StrategyObservation(ticker='TEST',observed_at=NOW,price=10.,bid=9.99,ask=10.01,
        macd_line=gap/1000,macd_signal=0.,source_timeframe='100ms',evaluation_events=('bar_close',),
        source_values=values,position_quantity=quantity)
    return a,o


@pytest.mark.parametrize('gap,action',[(-5.01,'wait'),(-5.,'wait'),(-4.99,'enter_long'),(0.,'enter_long')])
def test_strict_entry_threshold(gap,action):
    a,o=fixture(gap)
    r=M.evaluate(a,o)
    assert r.evaluation.signals[0].action==action
    if action=='enter_long':
        i,=r.evaluation.intents
        assert i.quantity==100 and i.invalidation_price is None and i.profit_target_price is None
        assert i.protection_profile is None


@pytest.mark.parametrize('gap,action',[(-5.,'hold'),(-9.,'hold'),(-10.,'hold'),(-10.01,'exit')])
def test_exit_hysteresis_without_entry_gates(gap,action):
    a,o=fixture(gap,100)
    r=M.evaluate(replace(a,status=S.AssignmentStatus.MANAGING),replace(o,source_values={},bid=0,ask=0))
    assert r.evaluation.signals[0].action==action


def test_one_second_and_intrabar_values_do_not_trade():
    a,o=fixture()
    assert not M.evaluate(a,replace(o,source_timeframe='1s')).evaluation.intents
    assert not M.evaluate(a,replace(o,evaluation_events=('market_data_update',))).evaluation.intents


def test_gates_expire_on_their_own_clock_and_no_other_policy_is_called(monkeypatch):
    a,o=fixture()
    host=S.LongMomentumStrategyEngine(revision=47)
    monkeypatch.setattr(host,'_evaluate',lambda *args:pytest.fail('Legacy evaluator called'))
    assert host.evaluate(a,replace(o,observed_at=NOW+timedelta(milliseconds=700))).evaluation.intents
    assert not host.evaluate(a,replace(o,observed_at=NOW+timedelta(milliseconds=2001))).evaluation.intents
    assert not host.evaluate(a,replace(o,ask=10.2)).evaluation.intents


def test_full_exit_then_reentry_needs_no_episode_high():
    a,o=fixture()
    entry=M.evaluate(a,o)
    a=replace(a,state=entry.state,status=S.AssignmentStatus.MANAGING)
    exit_=M.evaluate(a,replace(o,observed_at=NOW+timedelta(milliseconds=100),position_quantity=100,macd_line=-.011))
    assert exit_.evaluation.intents[0].action=='exit'
    pending=replace(a,state=exit_.state,status=S.AssignmentStatus.EXIT_PENDING)
    assert not M.evaluate(pending,replace(o,observed_at=NOW+timedelta(milliseconds=200))).evaluation.intents
    a=replace(pending,status=S.AssignmentStatus.REENTRY_COOLDOWN)
    assert M.evaluate(a,replace(o,observed_at=NOW+timedelta(milliseconds=300))).evaluation.intents[0].action=='enter_long'


@pytest.mark.parametrize('mode,expected_orders',[('backtest',1),('replay',0)])
def test_real_runtime_entry_has_no_bracket(tmp_path,mode,expected_orders):
    from tests import test_long_momentum_strategy as T
    from src.trading_runtime.journal import TradingJournal
    async def run():
        a,o=fixture()
        journal=TradingJournal(tmp_path/'threshold.sqlite3')
        broker=T.SimulatedBrokerAdapter(['sim'],mode=T.TradingMode.BACKTEST)
        strategy=S.AssignedLongMomentumStrategy([a])
        runtime=T.TradingRuntime(T.RunConfig(mode=T.RunMode(mode),strategy_id=S.STRATEGY_ID,
            strategy_revision=47,account_ids=('sim',),anchor_date=NOW.date(),run_id='threshold'),broker,strategy,journal,
            intent_planner=T.RuntimeIbkrStrategyOrderPlanner({'TEST':T.InstrumentContract('ibkr:123',123,'TEST','STK','USD')},
                strategy_id=S.STRATEGY_ID,strategy_revision=47))
        try:
            account=runtime.portfolio.states['sim']
            account.profile=replace(account.profile,policy=replace(account.profile.policy,allow_outside_rth=True))
            await runtime.initialize()
            await broker.on_market_event(T.QuoteEvent(ask_exchange=11,ask_price=10.01,ask_size=10000,
                bid_exchange=12,bid_price=9.99,bid_size=10000,conditions=(),indicators=(),ingest_ts=NOW,
                raw={'conid':123},sequence=1,source='test',tape=3,ticker='TEST',ts=NOW))
            await runtime.process_strategy_observation(o)
            orders=await broker.live_orders()
            assert len(orders)==expected_orders
            if orders:
                assert orders[0].orderType=='LMT' and not orders[0].parentId
                assert orders[0].totalSize==100
                async def tick(ms):
                    at=NOW+timedelta(milliseconds=ms)
                    await runtime.process_event(T.QuoteEvent(ask_exchange=11,ask_price=10.01,ask_size=10000,
                        bid_exchange=12,bid_price=9.99,bid_size=10000,conditions=(),indicators=(),ingest_ts=at,
                        raw={'conid':123},sequence=ms+1,source='test',tape=3,ticker='TEST',ts=at),
                        evaluate_strategy=False)
                for start in (0,300):
                    if start:
                        await runtime.process_strategy_observation(replace(o,observed_at=NOW+timedelta(milliseconds=start)))
                    await tick(start+10)
                    assert strategy.assignments()[0].status==S.AssignmentStatus.MANAGING
                    await runtime.process_strategy_observation(replace(o,observed_at=NOW+timedelta(milliseconds=start+100),macd_line=-.011,position_quantity=100))
                    await tick(start+110)
                    assert strategy.assignments()[0].status==S.AssignmentStatus.REENTRY_COOLDOWN
                assert all(float(p.position)==0 for p in await broker.positions('sim'))
                assert not any(row.payload.get('event')=='protection_repair_failed' for row in journal.records('threshold'))
        finally:
            journal.close()
    asyncio.run(run())
