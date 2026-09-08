import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpDown, LoaderCircle, SlidersHorizontal } from 'lucide-react';
import type { ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { HindsightDetails } from './HindsightPositions';

type Gap = { id: string; kind: 'support' | 'resistance'; lower: number; upper: number;
  valid_from: number; valid_to: number | null; width: number; width_bps: number };
type Setup = { id: number; time: number; price: number; support: number; support_lower: number;
  stop: number; target: number; score: number; upside: number; risk: number; reward_risk: number;
  volatility: number; cost: number; upside_gap: number; downside_gap: number | null;
  prior_retests: number; support_age_seconds: number; support_p_norm: number | null;
  outcome: string; outcome_at: number | null };
type Result = { segments: Gap[]; setups: Setup[]; seconds: number; book_id: string };
type Book = { id: string; ticker: string; version: string; start: string; end: string };
const defaults = { minimum_p_norm: .2, proximity_bps: 50, cost_bps: 20, minimum_gap_bps: 20, maximum_gaps: 3 };
const fields = [
  ['minimum_p_norm', 'Minimum p_norm', 0, 1, .05],
  ['proximity_bps', 'Support approach (bps)', 5, 300, 5],
  ['cost_bps', 'Round-trip cost allowance (bps)', 0, 200, 5],
  ['minimum_gap_bps', 'Minimum displayed gap (bps)', 0, 500, 10],
  ['maximum_gaps', 'Nearest gaps per side', 1, 10, 1],
] as const;
const dollars = (value: number) => `$${value.toFixed(4)}`;
const clock = (value: number) => new Date(value * 1000).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false });

/** Research preview only. Both setup visibility and outcome disclosure follow the chart cutoff. */
export function useStructureGaps(ticker: string, sessionDate: string | undefined, cutoff: number) {
  const identity = `${ticker}:${sessionDate}`;
  const [state, setState] = useState<{ identity: string; busy?: boolean; visible?: boolean; error?: string; result?: Result }>({ identity });
  const [books, setBooks] = useState<Book[]>([]);
  const [bookId, setBookId] = useState('');
  const [settings, setSettings] = useState(defaults);
  const [threshold, setThreshold] = useState(0);
  const [opacity, setOpacity] = useState(12);
  const [support, setSupport] = useState(true);
  const [resistance, setResistance] = useState(true);
  const [history, setHistory] = useState(true);
  const [outcomes, setOutcomes] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const close = useCallback(() => setAnchor(null), []);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    setState({ identity }); setBooks([]); setBookId(''); setAnchor(null); setSelectedId(null);
    const abort = new AbortController();
    if (sessionDate) void api<{ items: Book[] }>('/api/trading/backtest/structure-books', { signal: abort.signal })
      .then(({ items }) => {
        if (abort.signal.aborted) return;
        const matches = items.filter(b => b.ticker === ticker.toUpperCase() && ['causal-swing-closing-book-4','causal-swing-closing-book-5'].includes(b.version)
          && b.start <= sessionDate && b.end >= sessionDate);
        setBooks(matches); if (matches.length === 1) setBookId(matches[0].id);
      }).catch((e: unknown) => { if (!abort.signal.aborted) setState({ identity, error: String(e) }); });
    return () => { abort.abort(); controller.current?.abort(); };
  }, [identity, ticker, sessionDate]);
  const current = state.identity === identity ? state : { identity };
  const generate = () => {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    setState({ identity, busy: true }); setSelectedId(null);
    void api<Result>('/api/research/structure-gaps', { method: 'POST', signal: abort.signal, timeoutMs: 180000,
      body: JSON.stringify({ ticker, session_date: sessionDate, book_id: bookId, ...settings }) })
      .then(result => { if (!abort.signal.aborted) setState({ identity, visible: true, result }); })
      .catch((e: unknown) => { if (!abort.signal.aborted) setState({ identity, error: e instanceof Error ? e.message : String(e) }); });
  };
  const eligible = useMemo(() => current.result?.setups.filter(s => s.time <= cutoff && s.score >= threshold) ?? [], [current.result, cutoff, threshold]);
  const selected = eligible.find(s => s.id === selectedId) ?? eligible.at(-1);
  const index = selected ? eligible.indexOf(selected) : -1;
  const segments = useMemo(() => current.visible ? current.result?.segments.filter(g => g.valid_from <= cutoff
    && (history || g.valid_to === null || g.valid_to > cutoff) && (g.kind === 'support' ? support : resistance)) ?? [] : [],
    [current.visible, current.result, cutoff, support, resistance, history]);
  const activeGaps = segments.filter(g => g.valid_to === null || g.valid_to > cutoff);
  const setups = useMemo(() => current.visible ? eligible : [], [current.visible, eligible]);
  return { segments, setups, selected: current.visible ? selected : undefined, opacity,
    controls: sessionDate ? <div className="hindsight-controls">
      <button className="toolbar-button" type="button" disabled={current.busy} aria-pressed={Boolean(current.visible)}
        onClick={e => current.result ? setState(s => ({ ...s, visible: !s.visible })) : setAnchor(e.currentTarget)}>
        {current.busy ? <LoaderCircle size={15} /> : <ArrowUpDown size={15} />}<span>{current.busy ? 'Analyzing gaps…' : 'Gap analysis'}</span>
      </button>
      <button className="toolbar-button" type="button" aria-label="Gap analysis settings" aria-haspopup="dialog" aria-expanded={Boolean(anchor)} onClick={e => setAnchor(anchor ? null : e.currentTarget)}><SlidersHorizontal size={15} /></button>
      {anchor ? <HindsightDetails anchor={anchor} onClose={close} title="Gap analysis · v4">
        <p className="chart-settings-help">Research preview. Gaps use band edges and keep support/resistance separate. Other kinds of levels can lie inside a gap. Strategy unchanged.</p>
        <div className="hindsight-summary" role="status">{current.error || (current.busy ? 'Reading certified book and causal 1s bars…' : current.result ?
          `${eligible.length} setups as of ${clock(cutoff)} ET · ${current.result.seconds.toFixed(2)}s calculation` : 'Choose a swing book and preview. No historical rows are written.')}</div>
        <form className="chart-settings-section" onSubmit={e => { e.preventDefault(); generate(); }}>
          <label className="chart-setting-row">Source book<select aria-label="Gap source book" value={bookId} onChange={e => setBookId(e.target.value)}>
            <option value="">{books.length ? 'Choose book' : 'No matching certified swing book'}</option>
            {books.map(b => <option value={b.id} key={b.id}>{b.ticker} · v{b.version.split('-').at(-1)} · {b.start} – {b.end} · {b.id.slice(-6)}</option>)}
          </select></label>
          {fields.filter(([key]) => key !== 'minimum_p_norm' || books.find(b => b.id===bookId)?.version !== 'causal-swing-closing-book-5').map(([key, label, min, max, step]) => <label className="chart-setting-row" key={key}>{label}<span className="chart-setting-inline"><input aria-label={label} type="range" min={min} max={max} step={step} value={settings[key]} onChange={e => setSettings(s => ({ ...s, [key]: e.target.valueAsNumber }))} /><b>{Number(settings[key].toFixed(2))}</b></span></label>)}
          <button className="toolbar-button hindsight-apply" type="submit" disabled={current.busy || !bookId}>Apply and preview</button>
        </form>
        {current.result ? <>
          <p className="chart-settings-help">Loaded book: {current.result.book_id}. Settings above apply when regenerated.</p>
          <label className="chart-setting-row">Support gaps<input type="checkbox" checked={support} onChange={e => setSupport(e.target.checked)} /></label>
          <label className="chart-setting-row">Resistance gaps<input type="checkbox" checked={resistance} onChange={e => setResistance(e.target.checked)} /></label>
          <label className="chart-setting-row">Gap history<input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} /></label>
          <label className="chart-setting-row">Gap opacity<span className="chart-setting-inline"><input aria-label="Gap opacity" type="range" min={0} max={40} value={opacity} onChange={e => setOpacity(e.target.valueAsNumber)} /><b>{opacity}%</b></span></label>
          <label className="chart-setting-row">Minimum setup score<span className="chart-setting-inline"><input aria-label="Minimum setup score" type="range" min={0} max={100} value={threshold} onChange={e => setThreshold(e.target.valueAsNumber)} /><b>{threshold}</b></span></label>
          <p className="chart-settings-help">Current gaps (widths at detection): {activeGaps.length ? activeGaps.map(g => `${g.kind === 'support' ? 'S' : 'R'} ${dollars(g.lower)}–${dollars(g.upper)} (${g.width_bps.toFixed(0)} bps)`).join('; ') : 'None shown'}.</p>
          {selected ? <>
            <label className="chart-setting-row">Inspect setup<span className="structure-gap-navigation">
              <button type="button" className="toolbar-button" aria-label="Previous gap setup" disabled={index <= 0} onClick={() => setSelectedId(eligible[index - 1].id)}>‹</button>
              <input aria-label="Gap setup" type="range" min={0} max={Math.max(0, eligible.length - 1)} value={index} onChange={e => setSelectedId(eligible[e.target.valueAsNumber].id)} />
              <button type="button" className="toolbar-button" aria-label="Next gap setup" disabled={index >= eligible.length - 1} onClick={() => setSelectedId(eligible[index + 1].id)}>›</button>
            </span></label>
            <div className="hindsight-summary">G{selected.id} · {clock(selected.time)} ET · Score {selected.score.toFixed(1)}/100<br />
              Approach {dollars(selected.price)} · Support {dollars(selected.support_lower)}–{dollars(selected.support)}<br />
              Target {dollars(selected.target)} · Buffered stop {dollars(selected.stop)}<br />
              Net upside {dollars(selected.upside)} · Risk with costs {dollars(selected.risk)} · R/R {selected.reward_risk.toFixed(2)}<br />
              Upside gap {dollars(selected.upside_gap)} · Gap below support {selected.downside_gap === null ? 'unbounded / unknown' : dollars(selected.downside_gap)}<br />
              Prior noise {dollars(selected.volatility)} · Cost allowance {dollars(selected.cost)}<br />
              {selected.support_p_norm === null ? 'V5 support' : `Support p_norm ${selected.support_p_norm.toFixed(2)}`} · Prior retests (max member) {selected.prior_retests}
            </div>
            <label className="chart-setting-row">Show observed outcome<input type="checkbox" checked={outcomes} onChange={e => setOutcomes(e.target.checked)} /></label>
            {outcomes ? <p className="chart-settings-help">{selected.outcome_at !== null && selected.outcome_at <= cutoff ? `${selected.outcome.replaceAll('_', ' ')} at ${clock(selected.outcome_at)} ET` : 'Unresolved at this chart time.'} Outcomes are separate from the frozen setup score; these are price touches, not simulated fills.</p> : null}
          </> : <p className="chart-settings-help">No qualifying pre-touch approaches at this chart time and score threshold.</p>}
          <p className="chart-settings-help">Score = 100 × net upside / (net upside + risk with costs + prior 30-bar median true range). An experimental opportunity ranking, not bounce probability. Stop is below the support band with a tick/noise buffer. Support quality and downside gaps are shown separately, not learned weights. Markers require an approach from above before any support touch; fast moves can skip this zone.</p>
        </> : null}
      </HindsightDetails> : null}
    </div> : null };
}

/** Paint-only overlay: no autoscale contribution or navigation side effects. */
export class StructureGapPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<'Candlestick'> | null = null;
  private update?: () => void;
  private gaps: Gap[] = [];
  private setups: Setup[] = [];
  private selected?: Setup;
  private coordinate: (t: number) => number | null = () => null;
  private end = 0;
  private start = 0;
  private opacity = .12;
  private support = ''; private resistance = ''; private text = '';
  private readonly view: IPrimitivePaneView = { zOrder: () => 'top', renderer: () => ({ draw: target => {
    if (!this.series) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save(); ctx.font = '11px sans-serif';
      for (const g of this.gaps) {
        if (g.valid_from > this.end) continue;
        if (g.valid_to !== null && g.valid_to <= this.start) continue;
        const x = this.coordinate(g.valid_from), end = this.coordinate(Math.min(g.valid_to ?? this.end, this.end));
        const lo = this.series!.priceToCoordinate(g.lower), hi = this.series!.priceToCoordinate(g.upper);
        if (x === null || end === null || lo === null || hi === null) continue;
        ctx.fillStyle = ctx.strokeStyle = g.kind === 'support' ? this.support : this.resistance;
        ctx.globalAlpha = this.opacity; ctx.fillRect(x, hi, end - x, lo - hi);
        ctx.globalAlpha = .55; ctx.setLineDash([3, 5]); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, hi); ctx.lineTo(end, hi); ctx.moveTo(x, lo); ctx.lineTo(end, lo); ctx.stroke();
        if (g.valid_to === null || g.valid_to > this.end) {
          ctx.globalAlpha = .9; ctx.fillText(`${g.kind === 'support' ? 'S' : 'R'} gap ${g.width_bps.toFixed(0)}bps`, Math.max(2, Math.min(end - 112, mediaSize.width - 112)), (hi + lo) / 2);
        }
      }
      ctx.setLineDash([]);
      for (const s of this.setups) {
        if (s.time > this.end || s.time < this.start) continue;
        const x = this.coordinate(s.time), y = this.series!.priceToCoordinate(s.price);
        if (x === null || y === null || x < 0 || x > mediaSize.width) continue;
        ctx.globalAlpha = s.id === this.selected?.id ? 1 : .6;
        ctx.fillStyle = this.text; ctx.beginPath(); ctx.arc(x, y + 7, 3, 0, Math.PI * 2); ctx.fill();
        if (s.id === this.selected?.id) {
          ctx.fillText(`G${s.id} · ${s.score.toFixed(0)}`, x + 5, y + 20);
          for (const [value, label, color] of [[s.target, 'Target', this.support], [s.stop, 'Stop', this.resistance]] as const) {
            const py = this.series!.priceToCoordinate(value), ex = this.coordinate(this.end);
            if (py === null || ex === null) continue;
            ctx.strokeStyle = ctx.fillStyle = color; ctx.setLineDash([6, 3]);
            ctx.beginPath(); ctx.moveTo(x, py); ctx.lineTo(ex, py); ctx.stroke();
            ctx.fillText(`${label} G${s.id} ${dollars(value)}`, x + 5, py - 4); ctx.setLineDash([]);
          }
        }
      }
      ctx.restore();
    });
  } }) };
  attached({ series, requestUpdate }: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) { this.series = series as ISeriesApi<'Candlestick'>; this.update = requestUpdate; }
  detached() { this.series = null; this.update = undefined; }
  paneViews() { return [this.view]; }
  setState(state: { segments: Gap[]; setups: Setup[]; selected?: Setup; opacity: number }, coordinate: (t: number) => number | null, end: number, start: number) {
    this.gaps = state.segments; this.setups = state.setups; this.selected = state.selected;
    this.coordinate = coordinate; this.end = end; this.start = start; this.opacity = state.opacity / 100;
    const style = getComputedStyle(document.documentElement);
    this.support = style.getPropertyValue('--success').trim(); this.resistance = style.getPropertyValue('--danger').trim();
    this.text = style.getPropertyValue('--text-primary').trim(); this.update?.();
  }
}
