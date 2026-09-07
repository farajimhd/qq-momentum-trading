/** Preserve event time on a compressed chart; never snap confirmation backward. */
export function structureTimeCoordinate(
  time: number,
  candles: Array<{ time: number }>,
  duration: number,
  coordinate: (time: number) => number | null,
  logicalCoordinate: (index: number) => number | null,
): number | null {
  if (!candles.length || !Number.isFinite(time)) return null;
  let left = 0, right = candles.length;
  while (left < right) {
    const middle = left + Math.floor((right - left) / 2);
    if (candles[middle].time < time) left = middle + 1;
    else right = middle;
  }
  // Clipping a level that already existed before the loaded window is safe.
  if (left === 0 || candles[left]?.time === time) return coordinate(candles[left].time);
  const previous = candles[left - 1];
  const x1 = coordinate(previous.time);
  if (x1 === null) return null;
  if (left < candles.length) {
    const next = candles[left], x2 = coordinate(next.time);
    return x2 === null ? null : x1 + (x2 - x1) * (time - previous.time) / (next.time - previous.time);
  }
  // At the replay edge there may be no trade/candle at confirmation time.
  // Extend into the time-axis whitespace instead of painting on the last bar.
  const lastIndex = candles.length - 1;
  const base = logicalCoordinate(lastIndex), next = logicalCoordinate(lastIndex + 1);
  if (base === null || next === null || !(duration > 0)) return null;
  return x1 + (next - base) * (time - previous.time) / duration;
}
