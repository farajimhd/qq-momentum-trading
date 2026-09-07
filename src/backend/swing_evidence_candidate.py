"""Construct an immutable research candidate without editing existing profiles."""
from copy import deepcopy

from src.trading_runtime.strategy_engine import resolve_long_momentum_parameters
from src.trading_runtime.swing_evidence import CONTRACT, entry_rules

PROFILE_ID = "long-momentum-swing-evidence-v1"
PLAN_ID = "momentum-swing-evidence-v1-replay"
LABEL = "MACD open - selling veto - completed MACD exit"


def prepare(configuration, source_profile_id, source_plan_id, *, source_configuration=None):
    model = deepcopy(configuration)
    source = source_configuration or configuration
    if source_configuration is not None:
        # Carry the tested discovery/execution setup forward, while preserving
        # current approved profiles byte-for-byte instead of restoring stale ones.
        for section in ("market_discovery", "portfolio", "oms", "accounts", "sessions", "trading_actions", "run_plans"):
            model[section] = deepcopy(source[section])
        existing_ids = {p["profile_id"] for p in model["strategy"]["profiles"]}
        model["strategy"]["profiles"].extend(deepcopy(p) for p in source["strategy"]["profiles"] if p["profile_id"] not in existing_ids)
    profile = deepcopy(next(p for p in source["strategy"]["profiles"] if p["profile_id"] == source_profile_id))
    profile.update(profile_id=PROFILE_ID, name=LABEL, revision=1, publication_status="draft",
                   editable=True, protected=False, origin="user", derived_from_profile_id=source_profile_id,
                   description="Completed 1s MACD > signal at either sign; usable selling imbalance below -0.3 vetoes entry. Missing pressure is neutral. Last-red-close protective stop and completed MACD closure; no structural/VWAP entry gate or early-top exit.")
    p = deepcopy(profile["parameters"])
    p.update(swing_evidence_contract=CONTRACT, local_swing_management=False)
    profile["parameters"] = resolve_long_momentum_parameters(p, revision=int(profile["definition_revision"]))
    stages = entry_rules()
    new_rules = {}
    for key, stage in stages.items():
        rule = deepcopy(stage["rule_sets"][0])
        rule.update(name="Completed 1s MACD open" if key != "veto" else "MACD closed",
                    description="Either sign is allowed; comparison uses completed one-second values.",
                    protected=False, origin="user")
        new_rules[rule["rule_set_id"]] = rule
    existing = model["market_discovery"]["rule_sets"]
    model["market_discovery"]["rule_sets"] = [r for r in existing if r["rule_set_id"] not in new_rules] + list(new_rules.values())
    lifecycle = profile["lifecycle"]
    for phase in (lifecycle["initial_entry"], lifecycle["reentry"]["rules"]):
        for key, stage_source in (("opportunity", "trigger"), ("confirmation", "confirmation"), ("blockers", "veto")):
            phase[key] = {"expression": {"kind": "rule_set", "rule_set_id": stages[stage_source]["rule_sets"][0]["rule_set_id"]}}
    lifecycle["reentry"].update(cooldown_ms=0, require_new_confirmation=False)
    lifecycle["reentry"]["pullback_reclaim"]["enabled"] = False
    for route in lifecycle["exit"]["rule_sets"]:
        route["enabled"] = False
    model["strategy"]["profiles"] = [p for p in model["strategy"]["profiles"] if p["profile_id"] != PROFILE_ID] + [profile]
    model["strategy"]["active_profile_id"] = PROFILE_ID
    plan = deepcopy(next(p for p in source["run_plans"]["plans"] if p["run_plan_id"] == source_plan_id))
    plan.update(run_plan_id=PLAN_ID, name=LABEL, profile_id=PROFILE_ID)
    mandate_ids = []
    for original in source["portfolio"]["mandates"]:
        if original.get("run_plan_id") != source_plan_id:
            continue
        mandate = deepcopy(original)
        for key in ("mandate_id", "principal_id"):
            mandate[key] = mandate[key].replace(source_plan_id, PLAN_ID)
        mandate["run_plan_id"] = PLAN_ID
        mandate_ids.append(mandate["mandate_id"])
        model["portfolio"]["mandates"] = [m for m in model["portfolio"]["mandates"] if m["mandate_id"] != mandate["mandate_id"]] + [mandate]
    plan["mandate_ids"] = mandate_ids
    for original in source["sessions"]["strategy_deployments"]:
        if original.get("run_plan_id") != source_plan_id:
            continue
        deployment = deepcopy(original)
        deployment.update(run_plan_id=PLAN_ID, name=LABEL, portfolio_mandate_ids=mandate_ids,
                          strategy_deployment_id=original["strategy_deployment_id"].replace(source_plan_id, PLAN_ID))
        model["sessions"]["strategy_deployments"] = [d for d in model["sessions"]["strategy_deployments"] if d["strategy_deployment_id"] != deployment["strategy_deployment_id"]] + [deployment]
    model["run_plans"]["plans"] = [p for p in model["run_plans"]["plans"] if p["run_plan_id"] != PLAN_ID] + [plan]
    return model
