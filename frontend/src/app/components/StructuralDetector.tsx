import { useEffect, useMemo, useRef, useState } from 'react';
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { HindsightDetails } from './HindsightPositions';
import './structuralDetector.css';

type Candle = { time: number; endTime?: number; open: number; high: number; low: number; close: number };
type Event = { state: string; level: Record<string, unknown> };
export type StructuralState = { time: number; effective_at: number; state: string; reason: string;
  global_context: string; global_status: string; local_events: Event[]; global_events: Event[];
  body_baseline: number; candle: Candle; local_swings: Record<string, unknown>[];
  confirmed_swings: Record<string, unknown>[]; developing_swings?: Record<string, unknown>;
  macd: { histogram_bps: number; warmup: boolean; active?: boolean; episode_started_at?: number | null }; gap_before: boolean };
type Result = { rows: StructuralState[]; pending_count: number; global_available_count: number;
  global_book: { id: string; fingerprint: string } | null; context_start: number | null };
const defaults = { reversal_bps: 50, volatility_multiple: 2, body_half_life: 5,
  consolidation_body_multiple: .25, proximity_body_multiple: 1, macd_gap_bps: 25 };
const fields = [ ['reversal_bps', 'Minimum local reversal (bps)', 1, 1000, 1],
  ['volatility_multiple', 'Local volatility multiple', .1, 10, .1],
  ['body_half_life', 'Body average half-life (candles)', 1, 100, 1],
  ['consolidation_body_multiple', 'Consolidation body multiple', .01, 2, .05],
  ['proximity_body_multiple', 'Resistance proximity body multiple', .1, 10, .1],
  ['macd_gap_bps', 'MACD episode gap (bps; context only)', .1, 1000, 1] ] as const;
const codes: Record<string, string> = { advance: 'A', pullback: 'PB', recovery: 'REC', consolidation: 'C', decline: 'D', unknown: '?' };
const eventCodes: Record<string, string> = { resistance_forming: 'RF', resistance_confirmed: 'R',
  higher_low_confirmed: 'HL', swing_low_confirmed: 'L', breakout: 'BO', rejection: 'REJ', support_failure: 'SF',
  testing_resistance: 'TR', testing_support: 'TS', approaching_resistance: 'AR' };
const priority = ['support_failure', 'rejection', 'breakout', 'resistance_forming', 'resistance_confirmed', 'higher_low_confirmed', 'swing_low_confirmed', 'testing_resistance', 'testing_support', 'approaching_resistance'];
export const structuralLabel = (row: StructuralState) => {
  const events = [...row.local_events, ...row.global_events];
  const event = priority.find(state => events.some(e => e.state === state));
  return (codes[row.state] || '?') + (event ? '·' + eventCodes[event] : '');
};
const human = (s: string) => s.replaceAll('_', ' ');
const price = (value: unknown) => typeof value === 'number' ? value.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '—';
const clock = (t: number) => new Date(t*1000).toLocaleString('en-US', { timeZone: 'America/New_York', hour12: false });
const EMPTY: StructuralState[] = [];
function duration(timeframe: string) {
  if (timeframe === '1M' || timeframe === '1Y') return 86400;
  const match = /^(\d+)(ms|s|m|h|d|w)$/.exec(timeframe.toLowerCase());
  return match ? Number(match[1])*({ ms: .001, s: 1, m: 60, h: 3600, d: 86400, w: 604800 }[match[2]] || 0) : 0;
}
function candleEnd(candle: Candle, timeframe: string, seconds: number) {
  if (candle.endTime != null && Number.isFinite(candle.endTime)) return candle.endTime;
  const at = new Date(candle.time*1000);
  if (timeframe === '1M') { at.setUTCMonth(at.getUTCMonth()+1); return at.getTime()/1000; }
  if (timeframe === '1Y') { at.setUTCFullYear(at.getUTCFullYear()+1); return at.getTime()/1000; }
  return candle.time+seconds;
}

export function useStructuralDetector(ticker: string, timeframe: string, candles: Candle[], asOf: string | undefined, storageKey: string, splitAdjusted: boolean) {
  const [stored, setStored] = useState<{ key: string; enabled: boolean; settings: typeof defaults }>({ key: '', enabled: false, settings: defaults });
  useEffect(() => {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey+'.structural-detector') || '{}');
      setStored({ key: storageKey, enabled: value.enabled === true, settings: { ...defaults, ...value.settings } });
    } catch { setStored({ key: storageKey, enabled: false, settings: defaults }); }
  }, [storageKey]);
  const enabled = stored.key === storageKey && stored.enabled;
  const change = (next: typeof stored) => { setStored(next); localStorage.setItem(storageKey+'.structural-detector', JSON.stringify(next)); };
  const [state, setState] = useState<{ identity: string; result?: Result; error?: string; busy?: boolean }>({ identity: '' });
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [selectedTime, setSelectedTime] = useState<number | null>(null);
  const seconds = duration(timeframe);
  const rawCutoff = asOf && Number.isFinite(Date.parse(asOf)) ? Math.min(Date.now(), Date.parse(asOf))/1000 : Date.now()/1000;
  const cutoff = Math.min(rawCutoff, candles.length ? candleEnd(candles.at(-1)!, timeframe, seconds) : rawCutoff);
  const identity = `${ticker}:${timeframe}:${splitAdjusted}:${JSON.stringify(stored.settings)}`;
  // Forecasts never enter this request. End-time gating excludes a forming bar.
  const closed = useMemo(() => candles.filter(c => candleEnd(c, timeframe, seconds) <= cutoff), [candles, timeframe, seconds, cutoff]);
  const body = useMemo(() => enabled ? JSON.stringify({ ticker, timeframe, as_of: closed.length ? candleEnd(closed.at(-1)!, timeframe, seconds) : cutoff,
    candles: closed.map(c => ({ time: c.time, end: candleEnd(c, timeframe, seconds), open: c.open, high: c.high, low: c.low, close: c.close })),
    settings: stored.settings, split_adjusted: splitAdjusted }) : '', [enabled, ticker, timeframe, closed, seconds, cutoff, stored.settings, splitAdjusted]);
  const latestBody = useRef(body); latestBody.current = body;
  useEffect(() => {
    if (!enabled || !seconds) return;
    const controller = new AbortController();
    let timer = 0, previousBody = '';
    const refresh = async () => {
      const next = latestBody.current;
      if (next && next !== previousBody) {
        previousBody = next;
        setState(s => ({ ...s, busy: true, error: undefined }));
        try {
          const result = await api<Result>('/api/indicators/structural-detector', { method: 'POST', body: next, signal: controller.signal, timeoutMs: 120000 });
          if (!controller.signal.aborted) setState({ identity, result });
        } catch (error) {
          if (!controller.signal.aborted) setState({ identity, error: String(error) });
        }
      }
      if (!controller.signal.aborted) timer = window.setTimeout(refresh, 500);
    };
    timer = window.setTimeout(refresh, 150);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [enabled, identity, seconds]);
  const result = state.identity === identity ? state.result : undefined;
  const rows = useMemo(() => {
    const times = new Set(closed.map(c => c.time));
    return enabled ? (result?.rows || EMPTY).filter(row => row.effective_at <= cutoff && times.has(row.time)) : EMPTY;
  }, [enabled, result, cutoff, closed]);
  const rowMap = useMemo(() => new Map(rows.map(row => [row.time, row])), [rows]);
  const selected = selectedTime == null ? rows.at(-1) : rowMap.get(selectedTime) || rows.at(-1);
  const status = !seconds ? 'Unsupported candle duration' : state.error || (state.busy ? 'Updating candle states…' :
    result ? `${rows.length} closed candles · ${candles.length-closed.length} pending · V5 context ${result.global_available_count}/${rows.length}` : 'Enable to classify loaded candles');
  const checkbox = <label className="chart-setting-row structural-detector-option"><span>Structural detector <small>Local swings + global V5 context · every closed candle</small></span>
    <input type="checkbox" aria-label="Structural detector" checked={enabled} onChange={e => change({ ...stored, key: storageKey, enabled: e.target.checked })} /></label>;
  const controls = <>
    <button type="button" className="toolbar-button structural-detector-toolbar" aria-pressed={enabled} onClick={() => change({ ...stored, key: storageKey, enabled: !enabled })}>Structural detector</button>
    <button type="button" className="toolbar-button structural-detector-toolbar" aria-label="Structural detector details and settings" onClick={e => setAnchor(anchor ? null : e.currentTarget)}>States & settings</button>
    {enabled ? <span className="chart-data-status" role="status" title={status}>{state.error ? 'Detector unavailable' : state.busy ? 'Detecting…' : `${rows.length} states${result?.global_available_count ? ' · V5' : ' · global unavailable'}`}</span> : null}
    {anchor ? <HindsightDetails anchor={anchor} onClose={() => setAnchor(null)} title="Structural detector">
      {checkbox}<p role="status">{status}</p>
      <p className="chart-settings-help">A advance · PB pullback · REC recovery · C consolidation · D decline. RF forming resistance · R confirmed resistance · BO breakout · REJ rejection · SF support failure. Hover a candle to inspect it. ET times.</p>
      {result?.context_start ? <p className="chart-settings-help">Context begins {clock(result.context_start)}. Loading earlier candles or changing settings recalculates this indicator; future candles do not revise earlier decisions.</p> : null}
      {result?.global_book ? <p className="chart-settings-help">V5 book {result.global_book.id}</p> : null}
      {selected ? <>
        <label className="chart-setting-row">Inspect closed candle <input aria-label="Inspect detector candle" type="range" min={0} max={Math.max(0, rows.length-1)} value={rows.indexOf(selected)} onChange={e => setSelectedTime(rows[e.target.valueAsNumber].time)} /></label>
        <p>{clock(selected.time)} · {structuralLabel(selected)}</p>
        <div className="structural-detector-evidence"><strong>{human(selected.state)} · {human(selected.global_context)}</strong><p>{human(selected.reason)}</p>
          <p>Known at {clock(selected.effective_at)} · {human(selected.global_status)}</p>
          {[...selected.local_events.map(e => ({ ...e, scope: 'Local' })), ...selected.global_events.map(e => ({ ...e, scope: 'Global' }))].map((e, i) =>
            <p key={i}>{e.scope}: {human(e.state)} · {price(e.level.lower ?? e.level.price)}{e.level.upper != null ? '–'+price(e.level.upper) : ''}</p>)}
          <p>Recent body {selected.body_baseline.toFixed(4)} · MACD gap {selected.macd.histogram_bps.toFixed(1)} bps{selected.macd.warmup ? ' (warming up)' : ''}{selected.gap_before ? ' · gap before this candle' : ''}</p>
          <p>MACD context: {selected.macd.warmup ? 'insufficient history' : selected.macd.active ? 'active episode since '+clock(selected.macd.episode_started_at!) : 'outside episode'}</p>
          <details><summary>Swing evidence</summary><pre>{JSON.stringify({ developing: selected.developing_swings, prior_local_swings: selected.local_swings, confirmed_this_close: selected.confirmed_swings }, null, 2)}</pre></details>
        </div>
      </> : null}
      <details><summary>Detector parameters</summary>{fields.map(([key, label, min, max, step]) => <label className="chart-setting-row" key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={stored.settings[key]} onChange={e => {
        const value = e.target.valueAsNumber; if (Number.isFinite(value) && value >= min && value <= max) change({ ...stored, settings: { ...stored.settings, [key]: value } });
      }} /></label>)}</details>
    </HindsightDetails> : null}
  </>;
  return { rows, checkbox, controls, enabled, status, inspect: setSelectedTime };
}

/** Independent paint layer: labels cannot be hidden by trade marker selection. */
export class StructuralDetectorPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<'Candlestick'> | null = null;
  private chart: IChartApi | null = null;
  private update?: () => void;
  private rows: StructuralState[] = EMPTY;
  private coordinate: (t: number) => number | null = () => null;
  private colors: Record<string, string> = {};
  private background = '';
  private readonly view: IPrimitivePaneView = { zOrder: () => 'top', renderer: () => ({ draw: target => {
    if (!this.series || !this.chart) return;
    const range = this.chart.timeScale().getVisibleRange();
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save(); ctx.font = '10px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      let right = -Infinity;
      for (const row of this.rows) {
        if (range && typeof range.from === 'number' && row.time < range.from) continue;
        if (range && typeof range.to === 'number' && row.time > range.to) break;
        const x = this.coordinate(row.time), priceY = this.series!.priceToCoordinate(row.candle.low);
        if (x == null || priceY == null || x < 0 || x > mediaSize.width) continue;
        const label = structuralLabel(row), width = ctx.measureText(label).width+4;
        if (x-width/2 < right+2) continue;
        const y = Math.min(mediaSize.height-14, Math.max(0, priceY+8));
        ctx.fillStyle = this.background; ctx.fillRect(x-width/2, y-1, width, 13);
        ctx.fillStyle = this.colors[row.state] || this.colors.unknown;
        ctx.fillText(label, x, y); right = x+width/2;
      }
      ctx.restore();
    });
  } }) };
  attached({ series, chart, requestUpdate }: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) { this.series = series as ISeriesApi<'Candlestick'>; this.chart = chart; this.update = requestUpdate; }
  detached() { this.series = null; this.chart = null; this.update = undefined; }
  paneViews() { return [this.view]; }
  setState(rows: StructuralState[], coordinate: (t: number) => number | null) {
    this.rows = rows; this.coordinate = coordinate;
    const css = getComputedStyle(document.documentElement), color = (name: string) => css.getPropertyValue(name).trim();
    this.colors = { advance: color('--success'), pullback: color('--warning'), recovery: color('--primary'), decline: color('--danger'), consolidation: color('--text-muted'), unknown: color('--text-muted') };
    this.background = color('--surface'); this.update?.();
  }
}
