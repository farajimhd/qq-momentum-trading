import { useEffect, useMemo, useRef, useState } from 'react';
import type { IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time } from 'lightweight-charts';
import { api } from '../../api/client';
import { createRoot, type Root } from 'react-dom/client';
import { Modal } from './Modal';
import { Button } from './Button';
// The shared Modal styles currently live in the table stylesheet. Indicators
// must also work on chart-only pages where no table has loaded that stylesheet.
import './DataTable.css';
import './structuralDetector.css';

type Candle = { time: number; endTime?: number; open: number; high: number; low: number; close: number };
type Event = { state: string; level: Record<string, unknown>; band_id?: string; encounters?: number; rejection_closes?: number; source_ids?: string[] };
type Cycle = { number: number; attempts: number; failed_attempts: number; depth: number; candles: number; recovery_progress: number };
export type StructuralState = { time: number; effective_at: number; state: string; reason: string;
  global_context: string; global_status: string; local_events: Event[]; global_events: Event[];
  direction?: string; local_bias?: string; global_bias?: string; candle_shape?: { tags: string[] };
  progression?: { tags: string[]; cycle?: Cycle | null; closed_cycle?: Cycle | null; completed_cycles: number;
    crossed: { local: number; global: number }; lost: { local: number; global: number }; accepted_levels: number; accepted_by_scope: {local:number;global:number}; structure_context: string };
  retained_context?: { local: { tracked_bands: number; pending_retests: number }; global_context: { tracked_bands: number; pending_retests: number } };
  focus_interactions?: { local: {primary:Event|null;other_bands:number}; global_context: {primary:Event|null;other_bands:number} };
  body_baseline: number; candle: Candle; local_swings: Record<string, unknown>[];
  confirmed_swings: Record<string, unknown>[]; developing_swings?: Record<string, unknown>;
  macd: { histogram_bps: number; warmup: boolean; active?: boolean; direction?: number; episode_started_at?: number | null }; gap_before: boolean };
type Result = { rows: StructuralState[]; pending_count: number; global_available_count: number;
  global_book: { id: string; fingerprint: string } | null; context_start: number | null };
const defaults = { reversal_bps: 50, volatility_multiple: 2, body_half_life: 5,
  consolidation_body_multiple: .25, proximity_body_multiple: 1, macd_gap_bps: 25, tail_range_fraction: .5, indecision_body_fraction: .2, expansion_body_multiple: 1.5, expansion_body_fraction: .65,
  movement_body_multiple: .1, movement_min_bps: 1, deep_correction_multiple: 2, evidence_memory_candles: 1800, pressure_closes: 2 };
const fields = [ ['reversal_bps', 'Minimum local reversal (bps)', 1, 1000, 1],
  ['volatility_multiple', 'Local volatility multiple', .1, 10, .1],
  ['body_half_life', 'Body average half-life (candles)', 1, 100, 1],
  ['consolidation_body_multiple', 'Consolidation body multiple', .01, 2, .05],
  ['proximity_body_multiple', 'Level proximity body multiple', .1, 10, .1],
  ['macd_gap_bps', 'MACD episode gap (bps; context only)', .1, 1000, 1],
  ['tail_range_fraction', 'Tail minimum fraction of range', .1, 1, .05],
  ['indecision_body_fraction', 'Indecision maximum body fraction', .01, 1, .05],
  ['expansion_body_multiple', 'Expansion recent-body multiple', .1, 10, .1],
  ['expansion_body_fraction', 'Expansion minimum body fraction', .1, 1, .05],
  ['movement_body_multiple', 'Meaningful close progress · recent body multiple', .01, 2, .01],
  ['movement_min_bps', 'Minimum close progress (bps)', .01, 100, .1],
  ['deep_correction_multiple', 'Deep correction · starting body multiple', .5, 20, .5],
  ['evidence_memory_candles', 'Retain absent structural evidence (candles)', 10, 20000, 1],
  ['pressure_closes', 'Persistent pressure · rejection closes', 2, 20, 1] ] as const;
const human = (s: string) => s.replaceAll('_', ' ');
const labelFields = { state: 'Movement', direction: 'Direction', local: 'Local interactions', global: 'Global interactions',
  localBias: 'Local swing trend', globalBias: 'Global swing trend', shape: 'Candle shape', macd: 'MACD context', body: 'Recent body size', source: 'Global availability',
  progression: 'Progression', cycle: 'Recovery cycle', levelProgress: 'Levels crossed / lost', context: 'Combined structure context', retained: 'Retained structural history',
  localDetails: 'All local band details', globalDetails: 'All global band details' };
type LabelField = keyof typeof labelFields;
export type LabelRows = LabelField[][];
const defaultRows: LabelRows = [['state'], ['progression'], ['local'], ['global'], ['shape']];
const price = (n: unknown) => typeof n==='number' ? n.toLocaleString('en-US',{maximumFractionDigits:6}) : '?';
const eventText = (events: Event[]) => [...new Map(events.map(e => [e.state+':'+(e.band_id || JSON.stringify(e.level)),
  `${human(e.state)} ${e.level.lower!=null ? price(e.level.lower)+'–'+price(e.level.upper) : price(e.level.price)}${e.encounters ? ' #'+e.encounters : ''}${(e.rejection_closes || 0)>1 ? ' · '+e.rejection_closes+' rejection closes' : ''}`])).values()].join(' · ') || 'none';
export function structuralLabelRows(row: StructuralState, rows: LabelRows) {
  const p=row.progression, c=p?.cycle || p?.closed_cycle;
  const focused=(scope:'local'|'global_context',fallback:Event[]) => {
    const focus=row.focus_interactions?.[scope];
    return focus ? eventText(focus.primary ? [focus.primary] : [])+(focus.other_bands ? ` · +${focus.other_bands} other bands` : '') : eventText(fallback);
  };
  const values: Record<LabelField,string> = { state: human(row.state), direction: row.direction || 'unknown',
    local: 'L: '+focused('local',row.local_events), global: 'G: '+focused('global_context',row.global_events),
    localDetails:'Local details: '+eventText(row.local_events), globalDetails:'Global details: '+eventText(row.global_events),
    localBias: 'Local: '+(row.local_bias || 'unknown'), globalBias: 'Global: '+(row.global_bias || 'unknown'),
    shape: (row.candle_shape?.tags || ['unavailable']).map(human).join(' · '),
    macd: row.macd.warmup ? 'MACD warming up' : `MACD ${row.macd.histogram_bps.toFixed(1)} bps`,
    body: `Body ${row.body_baseline.toFixed(4)}`, source: human(row.global_status),
    progression: p?.tags.filter(tag => tag!=='levels_crossed' || !p.tags.includes('multiple_levels_crossed')).map(human).join(' · ') || 'unavailable',
    cycle: c ? `Cycle ${c.number} · attempt ${c.attempts} · ${c.failed_attempts} failed · depth ${price(c.depth)} · ${c.candles} candles · ${(c.recovery_progress*100).toFixed(0)}% recovered` : `${p?.completed_cycles || 0} completed cycles`,
    levelProgress: p ? `Crossed L${p.crossed.local}/G${p.crossed.global} · lost L${p.lost.local}/G${p.lost.global} · accepted L${p.accepted_by_scope?.local ?? 0}/G${p.accepted_by_scope?.global ?? 0}` : 'unavailable',
    context: `Local ${row.local_bias || 'unknown'} / global ${row.global_bias || 'unknown'}${row.local_bias==='bullish' && row.global_bias==='bearish' || row.local_bias==='bearish' && row.global_bias==='bullish' ? ' · opposing trends' : ''}`,
    retained: row.retained_context ? `History L${row.retained_context.local.tracked_bands}/G${row.retained_context.global_context.tracked_bands} bands (includes distant levels)` : 'unavailable' };
  return rows.filter(fields => fields.length).map(fields => fields.map(field => values[field]).join(' · '));
}
function readRows(value: unknown): LabelRows {
  return Array.isArray(value) ? value.slice(0,10).map(row => Array.isArray(row) ? row.filter((key): key is LabelField => typeof key==='string' && key in labelFields) : []) : defaultRows;
}
/** The same label component is used on candles and in the settings preview. */
export function StructuralCandleLabel({ row, layout }: { row: StructuralState; layout: LabelRows }) {
  if (!layout.some(fields => fields.length)) return null;
  return <div className="structural-candle-label" data-direction={row.direction} data-candle-time={row.time}>
    {structuralLabelRows(row,layout).map((text,index) => <div className="structural-candle-label-row" key={index}>{text}</div>)}
  </div>;
}
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
  const [stored, setStored] = useState<{ key: string; enabled: boolean; settings: typeof defaults; labelRows: LabelRows }>({ key: '', enabled: false, settings: defaults, labelRows: defaultRows });
  useEffect(() => {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey+'.structural-detector') || '{}');
      setStored({ key: storageKey, enabled: value.enabled === true, settings: { ...defaults, ...value.settings }, labelRows: readRows(value.labelRows) });
    } catch { setStored({ key: storageKey, enabled: false, settings: defaults, labelRows: defaultRows }); }
  }, [storageKey]);
  const enabled = stored.key === storageKey && stored.enabled;
  const change = (next: typeof stored) => { setStored(next); localStorage.setItem(storageKey+'.structural-detector', JSON.stringify(next)); };
  const [state, setState] = useState<{ identity: string; result?: Result; error?: string; busy?: boolean }>({ identity: '' });
  const [settingsOpen, setSettingsOpen] = useState(false);
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
  const status = !seconds ? 'Unsupported candle duration' : state.error || (state.busy ? 'Updating candle states…' :
    result ? `${rows.length} closed candles · ${candles.length-closed.length} pending · V5 context ${result.global_available_count}/${rows.length}` : 'Enable to classify loaded candles');
  const checkbox = <label className="chart-setting-row structural-detector-option"><span>Structural detector <small>Local swings + global V5 context · every closed candle</small></span>
    <input type="checkbox" aria-label="Structural detector" checked={enabled} onChange={e => change({ ...stored, key: storageKey, enabled: e.target.checked })} /></label>;
  const controls = <>
    <button type="button" className="toolbar-button structural-detector-toolbar" aria-pressed={enabled} onClick={() => change({ ...stored, key: storageKey, enabled: !enabled })}>Structural detector</button>
    <button type="button" className="toolbar-button structural-detector-toolbar" aria-label="Structural detector settings" onClick={() => setSettingsOpen(true)}>Detector settings</button>
    {enabled ? <span className="chart-data-status" role="status">{state.error ? 'Detector unavailable' : state.busy ? 'Detecting·' : `${rows.length} states${result?.global_available_count ? ' · V5' : ' · global unavailable'}`}</span> : null}
    {settingsOpen ? <Modal className="structural-detector-settings" title="Structural detector settings" onClose={() => setSettingsOpen(false)}>
      <div className="structural-detector-settings-body">
        {checkbox}<p className="chart-settings-help" role="status">{status}</p>
        <section className="chart-settings-section"><h3>Candle labels</h3>
          <p className="chart-settings-help">Choose the contents of each row. Labels appear below completed candles without hovering. Zoom in to separate dense labels. Empty rows are hidden.</p>
          <p className="chart-settings-help">Add Progression or Recovery cycle to an existing layout to see the new sequence evidence. Band # is the independent encounter number. Local/global rows show the primary band and count other bands; select All band details for the complete list. Retained history includes distant levels, not current tests.</p>
          {stored.labelRows.map((row,index) => <fieldset className="structural-label-row-config" key={index}>
            <legend>Row {index+1}</legend>
            <div className="structural-label-fields">{Object.entries(labelFields).map(([key,label]) => <label className="chart-setting-toggle" key={key}>
              <input type="checkbox" aria-label={`Row ${index+1}: ${label}`} checked={row.includes(key as LabelField)} onChange={e => change({ ...stored,
                labelRows: stored.labelRows.map((current,i) => i!==index ? current : e.target.checked ? [...current,key as LabelField] : current.filter(v => v!==key)) })} />{label}
            </label>)}</div>
            <Button variant="ghost" onClick={() => change({ ...stored,labelRows: stored.labelRows.filter((_,i) => i!==index) })}>Remove row {index+1}</Button>
          </fieldset>)}
          <div className="structural-label-actions"><Button disabled={stored.labelRows.length>=10} onClick={() => change({ ...stored,labelRows: [...stored.labelRows,[]] })}>Add row</Button>
            <Button variant="ghost" onClick={() => change({ ...stored,labelRows: defaultRows })}>Reset label rows</Button></div>
          {rows.length ? <div className="structural-label-preview"><span>Preview · latest completed candle</span><StructuralCandleLabel row={rows.at(-1)!} layout={stored.labelRows} /></div> : null}
        </section>
        <section className="chart-settings-section"><h3>Detection</h3>
          <p className="chart-settings-help">Local swings and available global V5 structure are evaluated at every close, in both directions. Candle shapes are evidence, not automatic reversal signals. Changes recalculate the loaded history.</p>
          {fields.map(([key,label,min,max,step]) => <label className="chart-setting-row" key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={stored.settings[key]} onChange={e => {
            const value=e.target.valueAsNumber; if (Number.isFinite(value) && value>=min && value<=max) change({ ...stored,settings: { ...stored.settings,[key]:value } });
          }} /></label>)}
        </section>
      </div>
      <div className="structural-label-actions"><Button variant="primary" onClick={() => setSettingsOpen(false)}>Done</Button></div>
    </Modal> : null}
  </>;
  return { rows, checkbox, controls, enabled, status, labelRows: stored.labelRows };
}

/** Chart primitive supplies coordinates; React owns the reusable label component. */
export class StructuralDetectorPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<'Candlestick'> | null = null;
  private chart: IChartApi | null = null;
  private update?: () => void;
  private rows: StructuralState[] = EMPTY;
  private layout: LabelRows = defaultRows;
  private coordinate: (t:number) => number | null = () => null;
  private host?: HTMLDivElement;
  private root?: Root;
  private frame = 0;
  private readonly view: IPrimitivePaneView = { zOrder: () => 'top', renderer: () => ({ draw: target => {
    target.useMediaCoordinateSpace(({mediaSize}) => {
      if (!this.series || !this.chart) return;
      if (this.host) {
        this.host.style.left=`${this.chart.priceScale('left').width()}px`;
        this.host.style.right=`${this.chart.priceScale('right').width()}px`;
        this.host.style.height=`${mediaSize.height}px`;
      }
      const range=this.chart.timeScale().getVisibleRange();
      const labels: { row: StructuralState; x:number; y:number }[]=[];
      for (const row of this.rows) {
        if (range && typeof range.from==='number' && row.time<range.from) continue;
        if (range && typeof range.to==='number' && row.time>range.to) break;
        const x=this.coordinate(row.time), y=this.series.priceToCoordinate(row.candle.low);
        if (x!=null && y!=null && x>=0 && x<=mediaSize.width && y>=0 && y<mediaSize.height) labels.push({row,x,y:y+8});
      }
      cancelAnimationFrame(this.frame);
      this.frame=requestAnimationFrame(() => {
        this.root?.render(<>{labels.map(({row,x,y}) => <div className="structural-label-anchor" key={row.time} style={{left:x,top:y}}><StructuralCandleLabel row={row} layout={this.layout} /></div>)}</>);
        // React commits before the next frame. Cull overlapping cards only;
        // all candle decisions remain retained and become visible on zoom.
        this.frame=requestAnimationFrame(() => {
          if (!this.host) return;
          const width=this.host.clientWidth;
          // Batch layout reads before writes; dragging must not force one
          // browser layout per candle. All coordinates honor the app zoom.
          const boxes=Array.from(this.host.querySelectorAll<HTMLElement>('.structural-label-anchor'),node =>
            ({node,width:node.offsetWidth,height:node.offsetHeight,x:parseFloat(node.style.left),y:parseFloat(node.style.top)}));
          const occupied: {left:number;right:number;top:number;bottom:number}[]=[];
          boxes.forEach(box => {
            const x=Math.max(box.width/2,Math.min(width-box.width/2,box.x));
            const rect={left:x-box.width/2,right:x+box.width/2,top:box.y,bottom:box.y+box.height};
            const overlap=occupied.some(r => rect.left<r.right+2 && rect.right>r.left-2 && rect.top<r.bottom+2 && rect.bottom>r.top-2);
            box.node.style.left=`${x}px`;
            box.node.style.visibility=overlap?'hidden':'visible';
            if (!overlap) occupied.push(rect);
          });
        });
      });
    });
  } }) };
  attached({series,chart,requestUpdate}: Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]) {
    this.series=series as ISeriesApi<'Candlestick'>; this.chart=chart; this.update=requestUpdate;
    this.host=document.createElement('div'); this.host.className='structural-label-layer';
    chart.chartElement().appendChild(this.host); this.root=createRoot(this.host);
  }
  detached() { cancelAnimationFrame(this.frame); this.root?.unmount(); this.host?.remove(); this.root=undefined; this.host=undefined; this.series=null; this.chart=null; this.update=undefined; }
  paneViews() { return [this.view]; }
  autoscaleInfo() {
    if (!this.rows.length || !this.layout.some(fields => fields.length)) return null;
    return { priceRange: null, margins: { above: 0, below: Math.min(180,this.layout.filter(fields=>fields.length).length*28) } };
  }
  setState(rows:StructuralState[],coordinate:(t:number)=>number|null,layout:LabelRows) { this.rows=rows; this.coordinate=coordinate; this.layout=layout; this.update?.(); }
}
