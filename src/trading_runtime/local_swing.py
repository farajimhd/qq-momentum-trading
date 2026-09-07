"""Event-driven local breakout and causal swing protection for the opt-in prototype."""
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR


def context(observation):
    pressure = observation.market_pressure
    try:
        age = (observation.observed_at-datetime.fromisoformat(pressure["observed_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return {}
    return pressure.get("micro", {}) if 0 <= age <= .25 else {}


def entry(observation, state, tick):
    micro = context(observation)
    previous = state.get("micro_breakout", {})
    now = observation.observed_at.timestamp()
    exit_at = datetime.fromisoformat(state["last_exit_at"]).timestamp() if state.get("last_exit_at") else 0
    if previous and previous["at"] <= exit_at:
        previous = {}
        state.pop("micro_breakout", None)
    previous_price = state.get("previous_observed_price")
    rising = previous_price is None or observation.price > previous_price
    # The prior-window high excludes the current 100ms bucket and triggering trade.
    ready = micro.get("trades", 0) >= 3 and micro.get("span_ms", 0) >= 200
    if ready and rising and "market_data_update" in observation.evaluation_events:
        width = max(tick, micro["high"]-micro["low"])
        threshold = micro["high"]+tick
        max_extension = max(3*width, observation.price*.008)
        expired_extension = previous and now-previous["at"] > .5 and observation.price > previous["boundary"]
        fresh_base = width <= max(4*tick, observation.price*.001)
        existing_signal = previous and 0 <= now-previous["at"] <= .5 and observation.price > previous["boundary"]
        if not existing_signal and not (expired_extension and not fresh_base) and observation.price > threshold and observation.price-micro["low"] <= max_extension:
            previous = dict(at=now, boundary=threshold, low=micro["low"], width=width,
                            maximum_extension=max_extension, kind="range_breakout" if width <= 4*tick else "impulse_breakout")
            state["micro_breakout"] = previous
    active = bool(ready and rising and "market_data_update" in observation.evaluation_events
                  and previous and 0 <= now-previous["at"] <= .5
                  and observation.price > previous["boundary"]
                  and observation.price-previous["low"] <= previous["maximum_extension"])
    if state.get("last_exit_at"):
        active = active and previous.get("at", 0) > datetime.fromisoformat(state["last_exit_at"]).timestamp()
    return dict(previous, passed=active, observed_at=observation.observed_at.isoformat(),
                reason="micro_breakout" if active else "waiting_for_fresh_local_breakout")


def below(price, tick):
    step = Decimal(str(tick))
    return float((Decimal(str(price))/step).to_integral_value(rounding=ROUND_FLOOR)*step-step)


def update(observation, state, tick):
    swing = state.get("local_swing")
    identity = str(state.get("entry_at") or state.get("entry_reference_price") or observation.average_price)
    if not swing or swing.get("entry_identity") != identity:
        micro = context(observation)
        width = max(tick*2, float(micro.get("high", observation.price))-float(micro.get("low", observation.price)))
        spread = max(0., observation.ask-observation.bid)
        reversal = max(2*tick, min(spread*1.5, observation.price*.0075), min(width*.25, observation.price*.0075))
        swing = dict(entry_identity=identity, phase="up", high=max(observation.price, observation.average_price),
                     low=observation.price, protected_low=float(state.get("active_stop") or 0)+tick,
                     reversal=reversal, confirmed_lows=0)
        state["local_swing"] = swing
    swing["exit"] = False
    if "market_data_update" not in observation.evaluation_events:
        return swing
    price = observation.price
    distance = swing["reversal"]
    if swing["phase"] == "up":
        swing["high"] = max(swing["high"], price)
        if swing["high"]-price >= distance:
            swing.update(phase="pullback", reference_high=swing["high"], low=price,
                         peak_confirmed_at=observation.observed_at.isoformat())
    elif swing["phase"] == "pullback":
        swing["low"] = min(swing["low"], price)
        if price-swing["low"] >= distance:
            if swing["low"] > swing["protected_low"]:
                swing.update(protected_low=swing["low"], low_confirmed_at=observation.observed_at.isoformat())
                swing["confirmed_lows"] += 1
            swing.update(phase="rally", rally_high=price)
    if swing["phase"] == "rally":
        swing["rally_high"] = max(swing["rally_high"], price)
        if price > swing["reference_high"]+tick:
            swing.update(phase="up", high=price)
        elif swing["rally_high"]-price >= distance:
            pressure = state.get("market_pressure", {})
            fast = pressure.get("fast", {})
            selling = pressure.get("usable") and fast.get("trade_imbalance", 0) <= -.3 and fast.get("quote_imbalance", 0) < 0
            if selling:
                swing.update(exit=True, reason="local_high_retest_failed")
            else:
                swing.update(phase="pullback", low=price)
    swing["stop"] = below(swing["protected_low"], tick)
    swing["observed_at"] = observation.observed_at.isoformat()
    return swing
