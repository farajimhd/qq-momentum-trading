"""ClickHouse-native causal OHLC; no market events leave the server."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo
from research.mlops.clickhouse import (ClickHouseHttpClient, default_clickhouse_url,
    default_clickhouse_user, default_clickhouse_password)

NY = ZoneInfo('America/New_York')
SOURCE_VERSION = 'canonical-causal-ohlc-1'
HISTORICAL_POLICY = 'historical-sip-condition-v1'


def session_bounds(session):
    day = date.fromisoformat(session)
    return datetime.combine(day,time(4),NY), datetime.combine(day,time(20),NY)


def _query(sql):
    client = ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),
        default_clickhouse_password(),timeout_seconds=120,
        default_query_params=dict(readonly=1,max_threads=4,max_memory_usage=2147483648,
            max_result_rows=10000,max_result_bytes=8000000,result_overflow_mode='throw'))
    return [json.loads(line) for line in client.execute(sql+' FORMAT JSONEachRow').splitlines() if line]


def source_metadata(ticker,session,query=_query, *, policy=SOURCE_VERSION):
    if policy not in (SOURCE_VERSION, HISTORICAL_POLICY):
        raise ValueError('Unsupported swing source policy')
    if not re.fullmatch('[A-Z0-9.-]{1,20}',ticker):
        raise ValueError('Invalid ticker')
    date.fromisoformat(session)
    predicate = f"ticker='{ticker}' AND source_date='{session}'"
    days = query(f'SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE {predicate}')
    clocks = [] if policy == HISTORICAL_POLICY else query(f'SELECT * FROM q_live.historical_event_execution_clock_coverage_v1 FINAL WHERE {predicate}')
    rules = query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id")
    if policy == HISTORICAL_POLICY:
        if len(days) != 1 or not rules:
            raise ValueError(f'Missing certified SIP source or condition rules: {ticker} {session}')
        day = days[0]
        if int(day['event_count']) <= 0 or int(day['next_ordinal']) != int(day['last_ordinal']) + 1:
            raise ValueError(f'Invalid certified SIP continuity: {ticker} {session}')
        token = sha256(json.dumps([policy,days,rules],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return dict(token=token,source_version=policy,complete_for_history=True,request_complete=True),rules
    if len(days)!=1 or len(clocks)!=1:
        raise ValueError(f'Missing certified execution-clock coverage: {ticker} {session}')
    day,clock = days[0],clocks[0]
    if (int(day['event_count'])!=int(clock['event_count']) or int(clock['trade_count'])!=int(clock['clock_count']) or
        '|execution_clock_v1|' not in clock['source_filter_key'] or not clock['source_filter_key'].endswith('|delayed_audit_v1')):
        raise ValueError(f'Incomplete execution-clock coverage: {ticker} {session}')
    midnight = datetime.combine(date.fromisoformat(session),time(),NY)
    next_day = midnight+timedelta(days=1)
    start_us,end_us = int(midnight.timestamp()*1e6),int(next_day.timestamp()*1e6)
    years = '|'.join(map(str,range(midnight.year,(next_day-timedelta(microseconds=1)).astimezone(ZoneInfo('UTC')).year+1)))
    actual = query(f"""SELECT count() trades,countIf(c.found!=1 OR c.sip_timestamp_us!=e.sip_timestamp_us) missing
      FROM merge('market_sip_compact','^events_({years})$') AS e
      LEFT JOIN (SELECT ordinal,sip_timestamp_us,toUInt8(1) found FROM q_live.historical_event_execution_clock_v1 FINAL
        WHERE ticker='{ticker}' AND source_date='{session}') AS c ON c.ordinal=e.ordinal
      WHERE e.ticker='{ticker}' AND e.sip_timestamp_us>={start_us} AND e.sip_timestamp_us<{end_us} AND bitAnd(e.event_meta,1)=1""")
    if len(actual)!=1 or int(actual[0]['trades'])!=int(clock['trade_count']) or int(actual[0]['missing']):
        raise ValueError('Canonical execution clock rows do not match certified trades')
    token = sha256(json.dumps([SOURCE_VERSION,days,clocks,rules],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(token=token,source_version=SOURCE_VERSION,complete_for_history=True,request_complete=True),rules


def bar_sql(ticker,session,left,right,rules, *, policy=SOURCE_VERSION):
    if policy not in (SOURCE_VERSION, HISTORICAL_POLICY):
        raise ValueError('Unsupported swing source policy')
    tokens = lambda predicate:'['+','.join(str(r['token_id']) for r in rules if predicate(r))+']'
    known = tokens(lambda r:0<=r['modifier_int']<=65535)
    both = tokens(lambda r:r['update_last']==r['update_high_low']==1 or r['modifier_int'] in (0,12))
    form = tokens(lambda r:r['modifier_int']==12)
    price = tokens(lambda r:r['update_last']==1)
    extrema = tokens(lambda r:r['update_high_low']==1)
    start_us,end_us = int(left.timestamp()*1e6),int(right.timestamp()*1e6)
    years = '|'.join(map(str,range(left.astimezone(ZoneInfo('UTC')).year,
        (right-timedelta(microseconds=1)).astimezone(ZoneInfo('UTC')).year+1)))
    clock_join = '' if policy == HISTORICAL_POLICY else f"""INNER JOIN (SELECT ordinal,execution_timestamp_us FROM q_live.historical_event_execution_clock_v1 FINAL
      WHERE ticker='{ticker}' AND source_date='{session}' AND sip_timestamp_us>={start_us} AND sip_timestamp_us<{end_us}) AS c ON e.ordinal=c.ordinal"""
    clock_filter = '' if policy == HISTORICAL_POLICY else 'AND (c.execution_timestamp_us=0 OR intDiv(c.execution_timestamp_us,1000000)>=intDiv(e.sip_timestamp_us,1000000))'
    return f"""WITH arrayFilter(x->has({known},x),[condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5]) AS tokens,
      fromUnixTimestamp64Micro(toInt64(e.sip_timestamp_us),'America/New_York') AS local_time,
      toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) AS local_second,
      ((local_second<34200 OR local_second>=57600) AND hasAny(tokens,{form}) AND arrayAll(x->has({both},x),tokens)) AS form_t,
      arrayAll(x->has({price},x) OR (form_t AND has({form},x)),tokens) AS last_ok,
      arrayAll(x->has({extrema},x) OR (form_t AND has({form},x)),tokens) AS high_low_ok,
      toFloat64(price_primary_int)/if(bitAnd(event_meta,2)=2,10000.,100.) AS price
    SELECT intDiv(e.sip_timestamp_us,1000000)+1 AS t,
      maxIf(price,high_low_ok) AS high,minIf(price,high_low_ok) AS low,
      argMaxIf(price,tuple(e.sip_timestamp_us,e.ordinal),last_ok) AS close
    FROM merge('market_sip_compact','^events_({years})$') AS e
    {clock_join}
    WHERE e.ticker='{ticker}' AND e.sip_timestamp_us>={start_us} AND e.sip_timestamp_us<{end_us}
      AND bitAnd(event_meta,1)=1 AND price_primary_int>0 AND size_primary>0
      {clock_filter}
    GROUP BY t HAVING countIf(last_ok)>0 AND countIf(high_low_ok)>0 ORDER BY t"""


def read_session(ticker,session,client=None, *, policy=SOURCE_VERSION, query_workers=4):
    if not isinstance(query_workers,int) or isinstance(query_workers,bool) or not 1<=query_workers<=4:
        raise ValueError('Use 1..4 concurrent session queries')
    query = _query if client is None else lambda sql:client.query(sql,'causal_bars')
    revision,rules = source_metadata(ticker,session,query,policy=policy)
    start,end = session_bounds(session)
    def read(i):
        left,right = start+timedelta(hours=i*2),start+timedelta(hours=(i+1)*2)
        result = query(bar_sql(ticker,session,left,right,rules,policy=policy))
        return [(float(r['t']),float(r['high']),float(r['low']),float(r['close'])) for r in result]
    if query_workers==1:
        bars = [bar for i in range(8) for bar in read(i)]
    else:
        with ThreadPoolExecutor(max_workers=query_workers,thread_name_prefix='swing-clickhouse') as pool:
            bars = [bar for chunk in pool.map(read,range(8)) for bar in chunk]
    if source_metadata(ticker,session,query,policy=policy)[0]['token']!=revision['token']:
        raise ValueError('Canonical source changed during aggregation')
    if any(a[0]>=b[0] for a,b in zip(bars,bars[1:])):
        raise ValueError('Canonical seconds are not strictly ordered')
    return bars,revision
