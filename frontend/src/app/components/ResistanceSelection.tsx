import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Layers, SlidersHorizontal } from 'lucide-react';
import type { ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { HindsightDetails } from './HindsightPositions';

type Area = { id: string; price: number; lower: number; upper: number; score: number; selected: boolean;
  members: string[]; reasons: string[]; valid_from: number; valid_to: number | null };
type Result = { segments: Area[]; seconds: number; book_id: string };
type Book = { id: string; ticker: string; version: string; start: string; end: string };

export function useResistanceSelection(ticker: string, date: string | undefined, cutoff: number) {
  const identity = `${ticker}:${date}`;
  const [books, setBooks] = useState<Book[]>([]), [bookId, setBookId] = useState('');
  const [width, setWidth] = useState(100), [score, setScore] = useState(30), [opacity, setOpacity] = useState(80);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const close = useCallback(() => setAnchor(null), []);
  const [state, setState] = useState<{ identity: string; result?: Result; busy?: boolean; error?: string; visible?: boolean }>({ identity });
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController(); setState({ identity }); setBooks([]); setBookId(''); setAnchor(null);
    if (date) void api<{ items: Book[] }>('/api/trading/backtest/structure-books', { signal: abort.signal }).then(({ items }) => {
      if (abort.signal.aborted) return;
      const matches = items.filter(b => b.ticker === ticker.toUpperCase() && b.version === 'causal-swing-closing-book-4' && b.start <= date && b.end >= date);
      setBooks(matches); if (matches.length === 1) setBookId(matches[0].id);
    }).catch(e => { if (!abort.signal.aborted) setState({ identity, error: String(e) }); });
    return () => { abort.abort(); controller.current?.abort(); };
  }, [identity, ticker, date]);
  const current = state.identity === identity ? state : { identity };
  const generate = () => {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    setState({ identity, busy: true });
    void api<Result>('/api/research/resistance-selection', { method: 'POST', signal: abort.signal, timeoutMs: 180000,
      body: JSON.stringify({ ticker, session_date: date, book_id: bookId, maximum_width_bps: width, minimum_score: score }) })
      .then(result => { if (!abort.signal.aborted) setState({ identity, result, visible: true }); })
      .catch(e => { if (!abort.signal.aborted) setState({ identity, error: String(e) }); });
  };
  const segments = useMemo(() => current.visible ? current.result?.segments.filter(s => s.selected && s.valid_from <= cutoff) ?? [] : [], [current.visible, current.result, cutoff]);
  const active = useMemo(() => current.result?.segments.filter(s => s.valid_from <= cutoff && (s.valid_to === null || cutoff < s.valid_to)) ?? [], [current.result, cutoff]);
  return { segments, opacity, controls: date ? <div className="hindsight-controls">
    <button className="toolbar-button" type="button" disabled={current.busy || !bookId} aria-pressed={Boolean(current.visible)}
      title="Separate causal resistance-selection prototype; existing book and strategy unchanged"
      onClick={() => current.result ? setState(s => ({ ...s, visible: !s.visible })) : generate()}>
      <Layers size={15} /><span>{current.busy ? 'Selecting resistance…' : 'Selected resistance'}</span>
    </button>
    <button className="toolbar-button" type="button" aria-label="Resistance selection settings" aria-haspopup="dialog" aria-expanded={Boolean(anchor)} onClick={e => setAnchor(anchor ? null : e.currentTarget)}><SlidersHorizontal size={15} /></button>
    {anchor ? <HindsightDetails anchor={anchor} onClose={close} title="Resistance selection · prototype">
      <p className="chart-settings-help">Blue areas are selected from existing v4 resistance candidates. Evidence grades are not probabilities. Historical scores use the strongest member, never their sum. A new resistance role needs its own validation.</p>
      <div role="status" className="hindsight-summary">{current.error || (current.busy ? 'Reading the causal session…' : current.result ? `${active.filter(a => a.selected).length} selected / ${active.length} areas at chart time · ${current.result.seconds.toFixed(2)}s` : 'Choose a book and generate the overlay.')}</div>
      <form className="chart-settings-section" onSubmit={e => { e.preventDefault(); generate(); }}>
        <label className="chart-setting-row">V4 book<select value={bookId} onChange={e => setBookId(e.target.value)}><option value="">Select book</option>{books.map(b => <option key={b.id} value={b.id}>{b.ticker} · {b.start}–{b.end}</option>)}</select></label>
        {([['Maximum area width (bps)', width, setWidth, 10, 300, 10], ['Minimum evidence score', score, setScore, 0, 100, 5], ['Line opacity (%)', opacity, setOpacity, 0, 100, 5]] as const).map(([label, value, setter, min, max, step]) =>
          <label className="chart-setting-row" key={label}>{label}<span className="chart-setting-inline"><input type="range" aria-label={label} min={min} max={max} step={step} value={value} onChange={e => setter(e.target.valueAsNumber)} /><b>{value}</b></span></label>)}
        <button className="toolbar-button hindsight-apply" type="submit" disabled={!bookId || current.busy}>Apply and preview</button>
      </form>
      <p className="chart-settings-help">Width limits the entire area, preventing chains of small gaps from making one huge band. All prices are eligible, including those outside the old normalization range. No persistence or strategy integration.</p>
      <details><summary>Inspect areas at chart time ({active.length})</summary>{active.map(a => <div className="chart-settings-section" key={a.id}>
        <b>{a.selected ? 'Selected' : 'Excluded'} · ${a.lower.toFixed(3)}–${a.upper.toFixed(3)} · {a.score}/100</b>
        <p className="chart-settings-help">{a.reasons.join(' · ')}</p>
      </div>)}</details>
    </HindsightDetails> : null}
  </div> : null };
}

/** Paint only: does not alter chart range, zoom, or trading state. */
export class ResistanceSelectionPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<'Candlestick'> | null = null;
  private update?: () => void;
  private segments: Area[] = [];
  private coordinate: (t: number) => number | null = () => null;
  private end = 0; private start = 0; private opacity = .8; private color = '';
  private readonly view: IPrimitivePaneView = { zOrder: () => 'top', renderer: () => ({ draw: target => {
    if (!this.series) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save(); ctx.font = '11px sans-serif'; ctx.strokeStyle = ctx.fillStyle = this.color;
      for (const a of this.segments) {
        if (a.valid_from > this.end || (a.valid_to !== null && a.valid_to <= this.start)) continue;
        const x = this.coordinate(Math.max(this.start, a.valid_from)), ex = this.coordinate(Math.min(a.valid_to ?? this.end, this.end));
        const y = this.series!.priceToCoordinate(a.price), lo = this.series!.priceToCoordinate(a.lower), hi = this.series!.priceToCoordinate(a.upper);
        if (x === null || ex === null || y === null || lo === null || hi === null || lo < 0 || hi > mediaSize.height) continue;
        ctx.globalAlpha = this.opacity * .12; ctx.fillRect(x, hi, ex-x, lo-hi);
        ctx.globalAlpha = this.opacity; ctx.lineWidth = 2; ctx.setLineDash([7, 3]);
        ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(ex,y); ctx.stroke();
        if (a.valid_to === null || a.valid_to > this.end) ctx.fillText(`R ${a.price.toFixed(2)} · ${a.score}`, Math.max(2,ex-110), y-4);
      }
      ctx.restore();
    });
  } }) };
  attached({ series, requestUpdate }: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) { this.series = series as ISeriesApi<'Candlestick'>; this.update = requestUpdate; }
  detached() { this.series = null; this.update = undefined; }
  paneViews() { return [this.view]; }
  setState(state: { segments: Area[]; opacity: number }, coordinate: (t: number) => number | null, end: number, start: number) {
    this.segments = state.segments; this.opacity = state.opacity/100; this.coordinate = coordinate; this.end = end; this.start = start;
    this.color = getComputedStyle(document.documentElement).getPropertyValue('--resistance-selection').trim(); this.update?.();
  }
}
