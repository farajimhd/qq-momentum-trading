export function BacktestRecoveryFailure({ error, onSetup }: { error: string; onSetup: () => void }) {
  return <div className="canvas-inline-error" role="alert">
    <strong>Backtest failed</strong>
    <p>This run cannot be reopened for review. Return to setup to start a new backtest after resolving the failure.</p>
    <details><summary>Original failure details</summary><p style={{ overflowWrap: "anywhere" }}>{error}</p></details>
    <button className="button secondary compact" type="button" onClick={onSetup}>Return to setup</button>
  </div>;
}
