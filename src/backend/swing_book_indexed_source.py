"""Bounded, index-aligned V6 session reads; legacy reader remains the oracle.

The certified NY source-day ordinal interval narrows the existing timestamp
predicate. Price eligibility, tie ordering and OHLC expressions are unchanged.
"""
from hashlib import sha256
import json
from math import isfinite

from .swing_book_source import (
    HISTORICAL_POLICY, bar_sql, session_bounds, source_metadata, _query,
    ClickHouseHttpClient, default_clickhouse_url, default_clickhouse_user,
    default_clickhouse_password,
)

READER_VERSION = 'indexed-swing-session-1'
MAX_SESSION_BARS = 57_600  # One row per second, 04:00 through 20:00 NY.


def ordinal_bounds(day):
    count, next_ordinal, last = (int(day[k]) for k in ('event_count','next_ordinal','last_ordinal'))
    first = next_ordinal-count
    if count <= 0 or first < 0 or next_ordinal != last+1:
        raise ValueError('Invalid certified source-day ordinal bounds')
    if int(day['first_sip_timestamp_us']) > int(day['last_sip_timestamp_us']):
        raise ValueError('Invalid certified source-day timestamps')
    return first, next_ordinal


def session_sql(ticker, session, rules, day):
    first, stop = ordinal_bounds(day)
    left, right = session_bounds(session)
    sql = bar_sql(ticker,session,left,right,rules,policy=HISTORICAL_POLICY)
    needle = f"WHERE e.ticker='{ticker}'"
    if sql.count(needle) != 1:
        raise ValueError('Legacy OHLC query contract changed')
    return sql.replace(needle, f'{needle} AND e.ordinal>={first} AND e.ordinal<{stop}', 1)


def _session_query(sql):
    client = ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),
        default_clickhouse_password(),timeout_seconds=120,
        default_query_params=dict(readonly=1,max_threads=1,max_memory_usage=2147483648,
            max_result_rows=MAX_SESSION_BARS,max_result_bytes=8000000,result_overflow_mode='throw'))
    return [json.loads(line) for line in client.execute(sql+' FORMAT JSONEachRow').splitlines() if line]


def read_session(ticker, session, client=None):
    query = _query if client is None else lambda sql: client.query(sql,'causal_bars')
    revision, rules = source_metadata(ticker,session,query,policy=HISTORICAL_POLICY)
    days = query(f"SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker='{ticker}' AND source_date='{session}'")
    token = sha256(json.dumps([HISTORICAL_POLICY,days,rules],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if len(days) != 1 or token != revision['token']:
        raise ValueError('Certified source changed before indexed aggregation')
    sql = session_sql(ticker,session,rules,days[0])
    result = _session_query(sql) if client is None else client.query(sql,'causal_session_bars')
    if len(result) > MAX_SESSION_BARS:
        raise ValueError('Session OHLC exceeds the bounded second count')
    bars = [(float(r['t']),float(r['high']),float(r['low']),float(r['close'])) for r in result]
    start,end = (t.timestamp() for t in session_bounds(session))
    if any(not all(isfinite(v) for v in bar) or not start < bar[0] <= end
           or bar[0] != int(bar[0]) or bar[2] <= 0 or bar[1] < bar[2] or bar[3] <= 0 for bar in bars):
        raise ValueError('Invalid completed session OHLC')
    if any(a[0] >= b[0] for a,b in zip(bars,bars[1:])):
        raise ValueError('Canonical seconds are not strictly ordered')
    if source_metadata(ticker,session,query,policy=HISTORICAL_POLICY)[0]['token'] != revision['token']:
        raise ValueError('Canonical source changed during aggregation')
    return bars,revision
