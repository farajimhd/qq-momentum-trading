import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Layers, LoaderCircle, SlidersHorizontal } from 'lucide-react';
import type { ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { HindsightDetails } from './HindsightPositions';

type Segment = { level_id: number; price: number; lower: number; upper: number; side: 'support' | 'resistance';
  scale: 'local' | 'major'; valid_from: number; valid_to: number | null; state: string };
type Result = { segments: Segment[]; session_end: number; counts: Record<string, number>; active_levels: number;
  timing: { total_seconds: number; compute_seconds: number } };
const defaults = { reversal_bps: 50, volatility_multiple: 2, major_multiple: 3 };
const fields = [['reversal_bps', 'Minimum reversal (bps)', 10, 500, 10],
  ['volatility_multiple', 'Volatility multiple', .5, 6, .5], ['major_multiple', 'Major swing multiple', 1, 6, .5]] as const;
const EMPTY: Segment[] = [];

export function useSwingStructure(ticker: string, sessionDate?: string) {
  const identity = `${ticker}:${sessionDate}`;
  const [settings, setSettings] = useState(defaults);
  const [state, setState] = useState<{ identity: string; visible?: boolean; busy?: boolean; error?: string; result?: Result }>({ identity });
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const close = useCallback(() => setAnchor(null), []);
  const [showLocal, setShowLocal] = useState(false);
  const [lineOpacity, setLineOpacity] = useState(10);
  const [bandOpacity, setBandOpacity] = useState(5);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => { setState({ identity }); setAnchor(null); return () => controller.current?.abort(); }, [identity]);
  const current = state.identity === identity ? state : { identity };
  const generate = () => {
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    setState({ identity, busy: true });
    void api<Result>('/api/research/swing-structure', { method: 'POST', body: JSON.stringify({ ticker, session_date: sessionDate, ...settings }),
      signal: abort.signal, timeoutMs: 180000 }).then((result) => {
        if (!abort.signal.aborted) setState({ identity, visible: true, result });
      }).catch((error: unknown) => { if (!abort.signal.aborted) setState({ identity, error: error instanceof Error ? error.message : String(error) }); });
  };
  const segments = useMemo(() => current.visible && current.result ? current.result.segments.filter(s => showLocal || s.scale === 'major') : EMPTY,
    [current.visible, current.result, showLocal]);
  return { segments, end: current.result?.session_end ?? 0, lineOpacity, bandOpacity,
    controls: sessionDate ? <div className="hindsight-controls">
      <button type="button" className="toolbar-button" disabled={current.busy} aria-pressed={Boolean(current.visible)}
        title={current.error || 'Session-only causal swing structure. No historical books or strategy changes.'}
        onClick={() => current.result ? setState(s => ({ ...s, visible: !s.visible })) : generate()}>
        {current.busy ? <LoaderCircle size={15} /> : <Layers size={15} />}<span>{current.busy ? 'Finding swings…' : current.error ? 'Retry swing structure' : 'Swing structure'}</span>
      </button>
      <button type="button" className="toolbar-button" aria-label="Swing structure settings" aria-haspopup="dialog" aria-expanded={Boolean(anchor)} onClick={e => setAnchor(anchor ? null : e.currentTarget)}><SlidersHorizontal size={15} /></button>
      {anchor ? <HindsightDetails anchor={anchor} onClose={close} title="Swing structure prototype">
        <p className="chart-settings-help">Session only. Levels start after reversal confirmation. Dashed lines await breakout/retest confirmation. No prior-session levels; strategy unchanged.</p>
        <div className="hindsight-summary" role="status">{current.error || (current.busy ? 'Reading canonical one-second bars…' : current.result ?
          `${current.result.counts.confirmed_major ?? 0} major / ${current.result.counts.confirmed_local ?? 0} local levels · ${current.result.counts.expired ?? 0} retired · ${current.result.timing.total_seconds.toFixed(2)}s total (${current.result.timing.compute_seconds.toFixed(3)}s calculation)` : 'Generate a preview to inspect this session.')}</div>
        <label className="chart-setting-row">Show local swings<input type="checkbox" checked={showLocal} onChange={e => setShowLocal(e.target.checked)} /></label>
        {([['Price opacity', lineOpacity, setLineOpacity], ['Band opacity', bandOpacity, setBandOpacity]] as const).map(([label, value, setter]) =>
          <label className="chart-setting-row" key={label}>{label}<span className="chart-setting-inline"><input aria-label={label} type="range" min={0} max={100} value={value} onChange={e => setter(e.target.valueAsNumber)} /><b>{value}%</b></span></label>)}
        <form className="chart-settings-section" onSubmit={e => { e.preventDefault(); generate(); }}>
          {fields.map(([key, label, min, max, step]) => <label className="chart-setting-row" key={key}>{label}<span className="chart-setting-inline"><input type="range" aria-label={label} min={min} max={max} step={step} value={settings[key]} onChange={e => setSettings(s => ({ ...s, [key]: e.target.valueAsNumber }))} /><b>{settings[key]}</b></span></label>)}
          <p className="chart-settings-help">Local reversal = the larger of the bps floor, 2 ticks, and volatility × multiple. Major swings multiply that distance. Volatility is the median true range of the preceding 30 observed seconds; thresholds adapt as new bars close. Untested local levels expire after 30 minutes, major after 2 hours.</p>
          <button className="toolbar-button hindsight-apply" type="submit" disabled={current.busy}>Apply and preview</button>
        </form>
      </HindsightDetails> : null}
    </div> : null };
}

/** Paint-only: no series data, autoscale contribution, or trading decisions. */
export class SwingStructurePrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<'Candlestick'> | null = null;
  private update?: () => void;
  private segments: Segment[] = EMPTY;
  private coordinate: (t: number) => number | null = () => null;
  private end = 0;
  private line = .1;
  private band = .05;
  private support = '';
  private resistance = '';
  private readonly view: IPrimitivePaneView = { zOrder: () => 'bottom', renderer: () => ({ draw: target => {
    if (!this.series) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save();
      for (const s of this.segments) {
        if (s.valid_from >= this.end) continue;
        const x1 = this.coordinate(s.valid_from), x2 = this.coordinate(Math.min(s.valid_to ?? this.end, this.end));
        if (x1 === null || x2 === null || x2 < 0 || x1 > mediaSize.width) continue;
        const y = this.series!.priceToCoordinate(s.price), lo = this.series!.priceToCoordinate(s.lower), hi = this.series!.priceToCoordinate(s.upper);
        if (y === null || lo === null || hi === null) continue;
        ctx.fillStyle = ctx.strokeStyle = s.side === 'support' ? this.support : this.resistance;
        ctx.globalAlpha = this.band; ctx.fillRect(x1, hi, x2-x1, lo-hi);
        ctx.globalAlpha = this.line; ctx.lineWidth = s.scale === 'major' ? 2 : 1;
        ctx.setLineDash(s.state === 'active' ? [] : [4, 4]);
        ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
      }
      ctx.restore();
    });
  } }) };
  attached({ series, requestUpdate }: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) { this.series = series as ISeriesApi<'Candlestick'>; this.update = requestUpdate; }
  detached() { this.series = null; this.update = undefined; }
  paneViews() { return [this.view]; }
  setState(segments: Segment[], coordinate: (t: number) => number | null, end: number, line: number, band: number) {
    this.segments = segments; this.coordinate = coordinate; this.end = end; this.line = line/100; this.band = band/100;
    const style = getComputedStyle(document.documentElement);
    this.support = style.getPropertyValue('--success').trim(); this.resistance = style.getPropertyValue('--danger').trim();
    this.update?.();
  }
}
