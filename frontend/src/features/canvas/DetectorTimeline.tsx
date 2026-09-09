import { useMemo, useState } from "react";
import type { PreviewRow } from "./contracts";
import "./detectorTimeline.css";

type Decision = {
  effective_at: string; sequence: number; state: string; reason: string; action: string;
  strategy_action: string; strategy_reason: string; reference_price?: number;
  assignment_id?: string; support_boundary?: number;
  advance_high?: number; pullback_low?: number; recovery_floor?: number; entry_threshold?: number;
  resistance?: { lower?: number; upper?: number };
};
export type DetectorRow = Decision & { key: string; at: string; until: string };
const clock = (value: string) => new Intl.DateTimeFormat("en-GB", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
}).format(new Date(value));
const label = (value: string) => value.replaceAll("_", " ");
const price = (value?: number) => value == null ? "—" : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");

export function detectorRows(rows: PreviewRow[], ticker: string, asOf: string): DetectorRow[] {
  const result: DetectorRow[] = [];
  const last = new Map<string, string>();
  const previousByOwner = new Map<string, DetectorRow>();
  for (const row of [...rows].sort((a, b) => Date.parse(String(a.event_time)) - Date.parse(String(b.event_time)) || Number(a.sequence) - Number(b.sequence))) {
    if (row.ticker !== ticker || Date.parse(String(row.event_time)) > Date.parse(asOf)) continue;
    const plan = (row.chart_plan || row.gate_snapshot) as Record<string, unknown> | undefined;
    const d = plan?.continuation_detector as Decision | undefined;
    if (!d || !Number.isFinite(Date.parse(d.effective_at))) continue;
    const owner = String(d.assignment_id || row.strategy_id) + ":" + String(row.strategy_revision);
    const signature = `${d.sequence}:${d.strategy_action}:${d.strategy_reason}`;
    if (last.get(owner) === signature) continue;
    last.set(owner, signature);
    const previous = previousByOwner.get(owner);
    if (previous) previous.until = String(row.event_time);
    const next = { ...d, key: `${owner}:${row.sequence}`, at: String(row.event_time), until: asOf };
    result.push(next);
    previousByOwner.set(owner, next);
  }
  return result;
}

export function DetectorTimeline({ rows }: { rows: DetectorRow[] }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);
  const filtered = useMemo(() => rows.filter((row) => (!from || clock(row.until) >= from) && (!to || clock(row.at) <= to)), [rows, from, to]);
  const pages = Math.max(1, Math.ceil(filtered.length / 50));
  const current = Math.min(page, pages - 1);
  if (!rows.length) return null;
  return <details className="strategy-definition-section detector-timeline">
    <summary>Detector decisions · {rows.length} intervals · ET</summary>
    <div className="detector-timeline-controls">
      <label>From (ET) <input aria-label="Detector from ET" type="time" step="1" value={from} onChange={(e) => { setFrom(e.target.value); setPage(0); }} /></label>
      <label>To (ET) <input aria-label="Detector to ET" type="time" step="1" value={to} onChange={(e) => { setTo(e.target.value); setPage(0); }} /></label>
      <button type="button" onClick={() => { setFrom(""); setTo(""); setPage(0); }}>Full period</button>
      <span>{filtered.length} intervals · page {current + 1}/{pages}</span>
      <button type="button" disabled={!current} onClick={() => setPage(current - 1)}>Previous</button>
      <button type="button" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>Next</button>
    </div>
    <p>Detector observations and actual strategy actions. Entry eligible still requires all entry rules and execution approval. Times show when evidence became available.</p>
    <div className="detector-timeline-table"><table><thead><tr><th>From–until (ET)</th><th>Detector / reason</th><th>Strategy action / reason</th><th>Evidence at decision</th></tr></thead>
      <tbody>{filtered.slice(current * 50, (current + 1) * 50).map((row) => <tr key={row.key}>
        <td>{clock(row.at)}–{clock(row.until)}</td>
        <td><strong>{label(row.state)}</strong><br />{label(row.reason)}</td>
        <td><strong>{label(row.strategy_action)}</strong><br />{label(row.strategy_reason)}</td>
        <td>Price {price(row.reference_price)} · high {price(row.advance_high)} · pullback low {price(row.pullback_low)}<br />
          Recovery floor {price(row.recovery_floor)} · support {price(row.support_boundary)} · entry &gt; {price(row.entry_threshold)}<br />
          Resistance {price(row.resistance?.lower)}–{price(row.resistance?.upper)}</td>
      </tr>)}</tbody></table>{!filtered.length ? <p>No detector intervals overlap this period.</p> : null}</div>
  </details>;
}
