import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Eye, EyeOff, LoaderCircle, SlidersHorizontal, X } from "lucide-react";
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time } from "lightweight-charts";
import { api } from "../../api/client";

export type HindsightPosition = {
  entry_time: number; exit_time: number; entry_price: number; exit_price: number;
  net_profit_per_share: number; net_return_bps: number;
  position_number: number;
};
type Result = { positions: HindsightPosition[]; position_count: number; interval_count?: number; interval_rejections?: Record<string, number>; rejection_reasons?: Record<string, number>; eligible_quotes?: number; net_profit_per_share: number;
  profit_filter?: { cutoff_bps: number; retained_count: number; removed_count: number; retained_profit_per_share: number } };
type Job = { id: string; status: "queued" | "running" | "completed" | "failed"; stage?: string; quotes: number; error?: string; result?: Result };
const EMPTY: HindsightPosition[] = [];
const LIQUIDITY_DEFAULTS = { lookback_seconds: 2, max_spread_bps: 100, min_displayed_shares: 100, activity_window_seconds: 1, min_trade_count: 3, min_trade_volume: 100 };
const LIQUIDITY_FIELDS = [
  ['lookback_seconds', 'Buy lookback (seconds)', 0, 30, .1],
  ['max_spread_bps', 'Maximum spread (bps)', 0, 500, 1],
  ['min_displayed_shares', 'Minimum shares on each side', 1, 10000, 1],
  ['activity_window_seconds', 'Trade activity window (seconds)', .1, 10, .1],
  ['min_trade_count', 'Minimum trades in window', 1, 100, 1],
  ['min_trade_volume', 'Minimum traded shares in window', 1, 10000, 1],
] as const;

function HindsightDetails({ anchor, onClose, children }: { anchor: HTMLButtonElement; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 8, top: 8, maxHeight: window.innerHeight - 16 });
  useLayoutEffect(() => {
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const box = panel.current?.getBoundingClientRect();
      const zoom = panel.current ? Number.parseFloat(getComputedStyle(panel.current).zoom) || 1 : 1;
      if (box) setPosition({ left: Math.max(8, Math.min(rect.left, window.innerWidth - box.width - 8)) / zoom,
        top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - box.height - 8)) / zoom,
        maxHeight: (window.innerHeight - 16) / zoom });
    };
    place();
    const observer = new ResizeObserver(place);
    if (panel.current) observer.observe(panel.current);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { observer.disconnect(); window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [anchor]);
  useEffect(() => {
    panel.current?.focus();
    const pointer = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && !anchor.contains(event.target as Node)) onClose();
    };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); anchor.focus(); } };
    document.addEventListener("pointerdown", pointer);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", pointer); document.removeEventListener("keydown", key); };
  }, [anchor, onClose]);
  return createPortal(<div ref={panel} className="chart-settings-slot hindsight-details" role="dialog" aria-label="Hindsight statistics and filter" tabIndex={-1} style={position}>
    <div className="chart-settings-header"><b>Hindsight statistics</b><button type="button" className="toolbar-button" aria-label="Close hindsight statistics" onClick={() => { onClose(); anchor.focus(); }}><X size={14} /></button></div>
    {children}
  </div>, document.body);
}

export function useHindsightPositions(ticker: string, sessionDate?: string) {
  const identity = `${ticker}:${sessionDate ?? ""}`;
  const [state, setState] = useState<{ identity: string; job?: Job; visible: boolean; error?: string }>({ identity, visible: false });
  const [settings, setSettings] = useState(LIQUIDITY_DEFAULTS);
  const [request, setRequest] = useState({ identity: "", nonce: 0, settings: LIQUIDITY_DEFAULTS });
  const generation = useRef(0);
  const [hideSmallProfits, setHideSmallProfits] = useState(true);
  const [detailsAnchor, setDetailsAnchor] = useState<HTMLButtonElement | null>(null);
  const closeDetails = useCallback(() => setDetailsAnchor(null), []);
  const current = state.identity === identity ? state : { identity, visible: false };
  useEffect(() => {
    generation.current += 1;
    setState({ identity, visible: false });
    setRequest({ identity: "", nonce: 0, settings: LIQUIDITY_DEFAULTS });
    setDetailsAnchor(null);
  }, [identity]);
  useEffect(() => {
    if (request.identity !== identity || !sessionDate) return;
    const controller = new AbortController();
    const epoch = ++generation.current;
    let timer: number | undefined;
    const receive = (job: Job) => {
      if (controller.signal.aborted || epoch !== generation.current) return;
      setState((value) => ({ ...value, identity, job, error: job.error }));
      if (job.status === "running" || job.status === "queued") {
        timer = window.setTimeout(() => {
          void api<Job>(`/api/research/hindsight/${job.id}`, { signal: controller.signal, timeoutMs: 15_000 }).then(receive).catch(failed);
        }, 1000);
      }
    };
    const failed = (error: unknown) => {
      if (!controller.signal.aborted && epoch === generation.current)
        setState({ identity, visible: false, error: error instanceof Error ? error.message : "Hindsight request failed" });
    };
    setState({ identity, visible: true });
    void api<Job>("/api/research/hindsight", { method: "POST", body: JSON.stringify({ ticker, session_date: sessionDate, cost_bps: 5, ...request.settings }), signal: controller.signal, timeoutMs: 15_000 }).then(receive).catch(failed);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [request, identity, ticker, sessionDate]);
  const busy = Boolean(request.identity === identity && !current.error && (!current.job || ["queued", "running"].includes(current.job.status)));
  const result = current.job?.result;
  const filter = result?.profit_filter;
  const displayedCount = hideSmallProfits && filter ? filter.retained_count : result?.position_count;
  const displayedProfit = hideSmallProfits && filter ? filter.retained_profit_per_share : result?.net_profit_per_share;
  const title = `Hindsight only · ${sessionDate} 04:00–20:00 New York · one share, long-only · completed-1s MACD > signal, including negative MACD. Buy the lowest liquid ask in the opening lookback; sell the highest liquid bid while open. Observed quotes only; size, spread and preceding trade activity gates apply. Costs: 5 bps per side. No latency or impact model; not a live signal or a global profit optimum.`;
  const controls = sessionDate ? <div className="hindsight-controls">
    <button type="button" className="toolbar-button" aria-label="Hindsight positions" aria-pressed={current.visible} disabled={busy} title={title}
      onClick={() => result ? setState((value) => ({ ...value, visible: !value.visible })) : setRequest((n) => ({ identity, nonce: n.nonce + 1, settings }))}>
      {busy ? <LoaderCircle size={15} /> : current.visible ? <EyeOff size={15} /> : <Eye size={15} />}
      <span>{busy ? current.job?.stage === "macd" ? "Finding MACD intervals…" : "Finding positions…" : "Hindsight"}</span>
    </button>
    <button type="button" className="toolbar-button" aria-label="Hindsight statistics and filter" aria-haspopup="dialog" aria-expanded={Boolean(detailsAnchor)} title="Hindsight statistics and filter" onClick={(event) => setDetailsAnchor(detailsAnchor ? null : event.currentTarget)}><SlidersHorizontal size={15} /></button>
    {detailsAnchor ? <HindsightDetails anchor={detailsAnchor} onClose={closeDetails}>
    <span className="hindsight-summary" role="status" title={current.error || title}>
      {current.error ? `Failed: ${current.error} — click Hindsight to retry` : busy ? `${(current.job?.quotes ?? 0).toLocaleString()} quotes` : result ? `${(result.interval_count ?? 0).toLocaleString()} MACD intervals · ${result.position_count.toLocaleString()} valid positions · ${current.visible ? `${displayedCount?.toLocaleString()} shown · $${displayedProfit?.toFixed(3)}/share` : "overlay hidden"}` : "Click Hindsight to generate positions for this session."}
    </span>
    {filter && current.visible ? <label className="hindsight-summary" title={title}>
      <input type="checkbox" checked={hideSmallProfits} onChange={(event) => setHideSmallProfits(event.target.checked)} /> Hide small profits
      {hideSmallProfits ? ` (<${filter.cutoff_bps.toFixed(1)} bps; ${filter.removed_count} hidden)` : ""}
    </label> : null}
    <p className="chart-settings-help">Minimum net return: 5% (500 bps), or the session’s lower quartile if higher. Profits include 5 bps cost per side.</p>
    {result ? <details className="chart-settings-help"><summary>Liquidity and rejected intervals</summary>
      <p>{result.eligible_quotes?.toLocaleString()} eligible quote updates</p>
      {Object.entries(result.interval_rejections ?? {}).map(([reason, count]) => <div key={reason}>{reason.replaceAll('_', ' ')}: {count.toLocaleString()}</div>)}
      {Object.entries(result.rejection_reasons ?? {}).map(([reason, count]) => <div key={reason}>Quotes — {reason.replaceAll('_', ' ')}: {count.toLocaleString()}</div>)}
    </details> : null}
    <form className="chart-settings-section" onSubmit={(event) => { event.preventDefault(); setRequest((n) => ({ identity, nonce: n.nonce + 1, settings })); }}>
      <h3>Liquidity and lookback</h3>
      {LIQUIDITY_FIELDS.map(([key, label, min, max, step]) => <label className="chart-setting-row" key={key}>{label}
        <span className="chart-setting-inline"><input aria-label={label} type="range" min={min} max={max} step={step} value={settings[key]} onChange={(event) => setSettings((value) => ({ ...value, [key]: event.target.valueAsNumber }))} /><b>{settings[key].toLocaleString()}</b></span>
      </label>)}
      <button type="submit" className="toolbar-button hindsight-apply" disabled={busy}>Apply and regenerate</button>
      <span className="chart-settings-help">Changes apply when regenerated. Quotes are never carried forward. Liquidity defaults are prototype filters, not guaranteed fills.</span>
    </form>
    </HindsightDetails> : null}
  </div> : null;
  const selected = useMemo(() => result && hideSmallProfits && filter ? result.positions.filter((p) => p.net_return_bps >= filter.cutoff_bps) : result?.positions, [result, hideSmallProfits, filter]);
  return { controls, positions: current.visible ? selected ?? EMPTY : EMPTY };
}

/** Independent paint-only layer: contributes nothing to autoscale or trade state. */
export class HindsightPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private update: (() => void) | undefined;
  private positions: HindsightPosition[] = EMPTY;
  private coordinate: (time: number) => number | null = () => null;
  private color = "";
  private backing = "";
  private readonly view: IPrimitivePaneView = {
    zOrder: () => "top",
    renderer: () => ({ draw: (target) => {
      if (!this.chart || !this.series || !this.positions.length) return;
      target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
        ctx.save();
        ctx.font = "11px sans-serif";
        ctx.lineWidth = 2;
        let lastLabelX = -Infinity;
        for (let index = 0; index < this.positions.length; index++) {
          const p = this.positions[index];
          const x1 = this.coordinate(p.entry_time), x2 = this.coordinate(p.exit_time);
          const y1 = this.series!.priceToCoordinate(p.entry_price), y2 = this.series!.priceToCoordinate(p.exit_price);
          if (y1 === null || y2 === null || (x1 === null && x2 === null)) continue;
          ctx.strokeStyle = this.color;
          ctx.fillStyle = this.color;
          ctx.setLineDash([5, 3]);
          if (x1 !== null && x2 !== null && x2 >= 0 && x1 <= mediaSize.width) {
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
          }
          ctx.setLineDash([]);
          for (const [x, y, entry] of [[x1, y1, true], [x2, y2, false]] as const) {
            if (x === null || x < 0 || x > mediaSize.width) continue;
            const sign = entry ? 1 : -1;
            ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - 4, y + sign * 8); ctx.lineTo(x + 4, y + sign * 8); ctx.closePath(); ctx.fill();
            // Keep every marker, but avoid stacking hundreds of labels at low zoom.
            if (x - lastLabelX >= 65) {
              const label = `${entry ? "Buy" : "Sell"} H${p.position_number ?? index + 1}`;
              const labelY = Math.max(14, Math.min(mediaSize.height - 5, y + sign * 22));
              ctx.fillStyle = this.backing;
              ctx.fillRect(x + 5, labelY - 12, ctx.measureText(label).width + 6, 15);
              ctx.fillStyle = this.color; ctx.fillText(label, x + 8, labelY);
              lastLabelX = x;
            }
          }
        }
        ctx.restore();
      });
    } }),
  };
  attached({ chart, series, requestUpdate }: Parameters<NonNullable<ISeriesPrimitive<Time>["attached"]>>[0]) {
    this.chart = chart; this.series = series as ISeriesApi<"Candlestick">; this.update = requestUpdate;
  }
  detached() { this.chart = null; this.series = null; this.update = undefined; }
  paneViews() { return [this.view]; }
  setState(positions: HindsightPosition[], coordinate: (time: number) => number | null) {
    this.positions = positions; this.coordinate = coordinate;
    const style = window.getComputedStyle(document.documentElement);
    this.color = style.getPropertyValue("--accent").trim();
    this.backing = style.getPropertyValue("--card").trim();
    this.update?.();
  }
}
