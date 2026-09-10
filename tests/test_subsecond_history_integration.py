"""Opt-in canonical history check: QMD_CHART_TEST_URL=http://127.0.0.1:8000.

Uses existing JUNS history; starts no backtest or gateway.
"""
import json
import os
import unittest
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen


@unittest.skipUnless(os.environ.get("QMD_CHART_TEST_URL"), "Running canonical history service required")
class SubsecondHistoryIntegrationTests(unittest.TestCase):
    def test_subsecond_prices_match_second_aggregation(self):
        def fetch(timeframe):
            params = urlencode(dict(
                symbol="JUNS", session_date="2026-08-21", as_of="2026-08-21T11:30:00Z",
                mode="replay", stage="bars", row_limit=5000, timeframe=timeframe,
                include_structure="false", include_market_signals="false",
            ))
            with urlopen(os.environ["QMD_CHART_TEST_URL"] + "/api/trading/canvas-chart/history?" + params, timeout=120) as response:
                return json.load(response)["history"]

        seconds = fetch("1s")
        subsecond = fetch("100ms")
        self.assertTrue(seconds)
        self.assertTrue(subsecond)
        groups = defaultdict(list)
        starts = []
        for bar in subsecond:
            start = datetime.fromisoformat(bar["bar_start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(bar["bar_end"].replace("Z", "+00:00"))
            self.assertEqual(round((end-start).total_seconds()*1000), 100)
            self.assertEqual(start.microsecond % 100000, 0)
            self.assertTrue(bar["is_closed"])
            self.assertGreaterEqual(bar["volume"], 0)
            starts.append(start)
            groups[start.replace(microsecond=0)].append(bar)
        self.assertEqual(starts, sorted(set(starts)))
        self.assertTrue(any(start.microsecond for start in starts))
        compared = 0
        for bar in seconds:
            start = datetime.fromisoformat(bar["bar_start"].replace("Z", "+00:00"))
            # The row-limited page can begin partway through its first second.
            if start <= starts[0].replace(microsecond=0) or start not in groups:
                continue
            rows = groups[start]
            # Price-less buckets are excluded from chart snapshots. Their
            # volume can contribute to a valid enclosing 1s bar, so displayed
            # subsecond volume is not an additive oracle for 1s volume.
            expected = dict(open=rows[0]["open"], close=rows[-1]["close"],
                            high=max(row["high"] for row in rows), low=min(row["low"] for row in rows))
            for key, value in expected.items():
                self.assertAlmostEqual(bar[key], value, places=7, msg=f"{start} {key}")
            compared += 1
        self.assertGreater(compared, 30)

    @unittest.skipUnless(os.environ.get("CHART_BROWSER_TEST_URL"), "Managed frontend required")
    def test_real_timeframe_switch_renders_subsecond_candles(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(os.environ["CHART_BROWSER_TEST_URL"])
                page.evaluate("""async()=>{
                  const React=(await import('/node_modules/.vite/deps/react.js')).default;
                  const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {useCanvasHistoricalChart}=await import('/src/features/canvas/chartData.ts');
                  const {ChartPreview}=await import('/src/features/canvas/chartPresentation.tsx');
                  const {StructuralDetectorPrimitive}=await import('/src/app/components/StructuralDetector.tsx');
                  const attached=StructuralDetectorPrimitive.prototype.attached;
                  StructuralDetectorPrimitive.prototype.attached=function(args){window.msNative=args;return attached.call(this,args)};
                  document.getElementById('root').style.display='none';
                  const node=document.createElement('div');document.body.appendChild(node);
                  const ids=[];
                  function Fixture(){
                    const [timeframe,setTimeframe]=React.useState('1s');window.msSwitch=setTimeframe;
                    const state=useCanvasHistoricalChart('JUNS',timeframe,Date.parse('2026-08-21T11:30:00Z'),'2026-08-21',ids,false,true,'replay',false);
                    window.msState=state;
                    return React.createElement(ChartPreview,{canvasId:'ms-qa',instanceId:'ms-qa',linkContext:{symbol:'JUNS'},changeAsOf:'2026-08-21T11:30:00Z',
                      chartSettings:{timeframe,visibleIndicators:ids,showSplitEvents:false},liveChart:state,
                      onChartSettingsChange:s=>setTimeframe(s.timeframe),onLinkContextChange:()=>{},symbolEditable:false});
                  }
                  window.msRoot=(dom.default??dom).createRoot(node);window.msRoot.render(React.createElement(Fixture));
                }""")
                page.wait_for_function("window.msState?.ready && !window.msState.loading && window.msNative?.series.data().length>0", timeout=120000)
                page.get_by_role("button", name="100ms", exact=True).click()
                page.wait_for_function("window.msState?.ready && !window.msState.loading && window.msNative?.series.data().some(b=>b.time%1!==0)", timeout=120000)
                page.wait_for_timeout(300)
                visible = page.evaluate("""()=>{
                  const {chart,series}=msNative;const width=chart.timeScale().width();
                  return series.data().filter(b=>{const x=chart.timeScale().timeToCoordinate(b.time),y=series.priceToCoordinate(b.close);
                    return x!==null && x>=0 && x<=width && y!==null && y>=0 && y<chart.paneSize(0).height}).length;
                }""")
                self.assertGreater(visible, 5)
                self.assertFalse(errors, errors)
                output = os.environ.get("CHART_TEST_SCREENSHOT")
                if output:
                    page.screenshot(path=output, full_page=True)
                page.get_by_role("button", name="1s", exact=True).click()
                page.wait_for_function("window.msState?.ready && !window.msState.loading && window.msState.bars[0]?.timeframe==='1s' && window.msNative.series.data().every(b=>b.time%1===0)", timeout=120000)
                self.assertFalse(errors, errors)
                page.evaluate("window.msRoot.unmount()")
            finally:
                browser.close()
