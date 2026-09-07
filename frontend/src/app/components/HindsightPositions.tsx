import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, EyeOff, LoaderCircle } from "lucide-react";
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time } from "lightweight-charts";
import { api } from "../../api/client";

export type HindsightPosition = {
  entry_time: number; exit_time: number; entry_price: number; exit_price: number;
  net_profit_per_share: number; net_return_bps: number;
  position_number: number;
};
type Result = { positions: HindsightPosition[]; position_count: number; unmerged_position_count?: number; net_profit_per_share: number;
  profit_filter?: { cutoff_bps: number; retained_count: number; removed_count: number; retained_profit_per_share: number } };
type Job = { id: string; status: "queued" | "running" | "completed" | "failed"; stage?: string; quotes: number; error?: string; result?: Result };
const EMPTY: HindsightPosition[] = [];

export function useHindsightPositions(ticker: string, sessionDate?: string) {
  const identity = `${ticker}:${sessionDate ?? ""}`;
  const [state, setState] = useState<{ identity: string; job?: Job; visible: boolean; error?: string }>({ identity, visible: false });
  const [request, setRequest] = useState({ identity: "", nonce: 0 });
  const generation = useRef(0);
  const [hideSmallProfits, setHideSmallProfits] = useState(true);
  const current = state.identity === identity ? state : { identity, visible: false };
  useEffect(() => {
    generation.current += 1;
    setState({ identity, visible: false });
    setRequest({ identity: "", nonce: 0 });
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
    void api<Job>("/api/research/hindsight", { method: "POST", body: JSON.stringify({ ticker, session_date: sessionDate, cost_bps: 5 }), signal: controller.signal, timeoutMs: 15_000 }).then(receive).catch(failed);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [request, identity, ticker, sessionDate]);
  const busy = Boolean(request.identity === identity && !current.error && (!current.job || ["queued", "running"].includes(current.job.status)));
  const result = current.job?.result;
  const filter = result?.profit_filter;
  const displayedCount = hideSmallProfits && filter ? filter.retained_count : result?.position_count;
  const displayedProfit = hideSmallProfits && filter ? filter.retained_profit_per_share : result?.net_profit_per_share;
  const title = `Hindsight only · ${sessionDate} 04:00–20:00 New York · one share, long-only · at least 1 share displayed on each side · spread ≤100 bps of midpoint · ask entries / bid exits + 5 bps per side · no latency or impact model. Merge gaps ≤1s or the same completed-1s MACD > signal interval (negative MACD allowed), retaining positive endpoint profit. Filter below the 25th percentile after merging. These merged/filtered moves are not a new profit optimum.`;
  const controls = sessionDate ? <div className="hindsight-controls">
    <button type="button" className="toolbar-button" aria-label="Hindsight positions" aria-pressed={current.visible} disabled={busy} title={title}
      onClick={() => result ? setState((value) => ({ ...value, visible: !value.visible })) : setRequest((n) => ({ identity, nonce: n.nonce + 1 }))}>
      {busy ? <LoaderCircle size={15} /> : current.visible ? <EyeOff size={15} /> : <Eye size={15} />}
      <span>{busy ? current.job?.stage === "macd" ? "Merging with MACD…" : "Finding positions…" : "Hindsight"}</span>
    </button>
    <span className="hindsight-summary" role="status" title={current.error || title}>
      {current.error ? `Failed: ${current.error} — click Hindsight to retry` : busy ? `${(current.job?.quotes ?? 0).toLocaleString()} quotes` : result ? `${(result.unmerged_position_count ?? result.position_count).toLocaleString()} found · ${result.position_count.toLocaleString()} after merge · ${current.visible ? `${displayedCount?.toLocaleString()} shown · $${displayedProfit?.toFixed(3)}/share` : "overlay hidden"}` : ""}
    </span>
    {filter && current.visible ? <label className="hindsight-summary" title={title}>
      <input type="checkbox" checked={hideSmallProfits} onChange={(event) => setHideSmallProfits(event.target.checked)} /> Hide small profits
      {hideSmallProfits ? ` (<${filter.cutoff_bps.toFixed(1)} bps; ${filter.removed_count} hidden)` : ""}
    </label> : null}
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
