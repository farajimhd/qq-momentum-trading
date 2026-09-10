export type SupertrendCandle = {time:number;endTime?:number;open:number;high:number;low:number;close:number};
export type SupertrendPoint = {time:number;effectiveAt:number;value:number;direction:1|-1;atr:number};

/** HL2 bands with Wilder ATR (SMA seed), standard recursive band ratchets.
 * Formula: https://www.tradingview.com/support/solutions/43000634738-supertrend/
 * Chart-only, closed 1-second observations. Missing seconds are not fabricated.
 * Loading an earlier prefix intentionally changes the seed, as with other ATRs.
 */
export function supertrend(candles:SupertrendCandle[], period=10, multiplier=3, cutoff=Infinity):SupertrendPoint[] {
  if (!Number.isInteger(period) || period<1 || period>500 || !Number.isFinite(multiplier) || multiplier<=0 || multiplier>100) throw Error('Invalid Supertrend settings');
  const output:SupertrendPoint[]=[];
  let previous:SupertrendCandle|undefined, atr:number|undefined, seed=0, count=0;
  let upper=0, lower=0, direction:1|-1=-1;
  for (const b of candles) {
    const end=b.endTime ?? b.time+1;
    if (end>cutoff) break;
    if (![b.time,end,b.open,b.high,b.low,b.close].every(Number.isFinite) || end<=b.time || b.low<=0 || b.low>Math.min(b.open,b.close) || b.high<Math.max(b.open,b.close) || previous && b.time<(previous.endTime ?? previous.time+1)) throw Error('Invalid Supertrend candle sequence');
    const tr=previous ? Math.max(b.high-b.low,Math.abs(b.high-previous.close),Math.abs(b.low-previous.close)) : b.high-b.low;
    count++;
    if (atr===undefined) { seed+=tr; if (count===period) atr=seed/period; }
    else atr=(atr*(period-1)+tr)/period;
    if (atr!==undefined) {
      const basicUpper=(b.high+b.low)/2+multiplier*atr, basicLower=(b.high+b.low)/2-multiplier*atr;
      if (!output.length) {upper=basicUpper;lower=basicLower;direction=-1;}
      else {
        const wasUpper=output[output.length-1].value===upper;
        upper=basicUpper<upper || previous!.close>upper ? basicUpper : upper;
        lower=basicLower>lower || previous!.close<lower ? basicLower : lower;
        direction=wasUpper ? (b.close>upper ? 1 : -1) : (b.close<lower ? -1 : 1);
      }
      output.push({time:b.time,effectiveAt:end,value:direction===1 ? lower : upper,direction,atr});
    }
    previous=b;
  }
  return output;
}
