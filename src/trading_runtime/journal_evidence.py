"""Lossless content-addressed journal evidence and a small read projection."""
from hashlib import sha256
import json

REFERENCE = '$journal_evidence_sha256'
CHART_LEVEL_FIELDS = frozenset({'unified_level_id', 'side', 'price', 'lower', 'upper',
    'entry_boundary', 'combined_entry_boundary', 'unified_break_boundary',
    'threshold_price', 'target_price'})
EVIDENCE_KEYS = frozenset({
    'unified_structural_trigger', 'profit_target_selection', 'protective_stop_selection',
    'v5_entry_selection', 'gap_selection', 'target_resistance_snapshot',
    'structural_level_snapshot', 'structural_resistance_levels', 'structural_support_levels',
    'decision_levels', 'closed_levels', 'levels', 'references',
})


def encode_evidence(value, dumps, evidence):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            encoded = encode_evidence(item, dumps, evidence)
            if key in EVIDENCE_KEYS and isinstance(item, (dict, list, tuple)) and item:
                raw = dumps(encoded)
                digest = sha256(raw.encode('utf-8')).hexdigest()
                evidence[digest] = raw
                result[key] = {REFERENCE: digest}
            else:
                result[key] = encoded
        return result
    if isinstance(value, (list, tuple)):
        return [encode_evidence(item, dumps, evidence) for item in value]
    return value


def decode_evidence(value, fetch, active=None):
    active = set() if active is None else active
    if isinstance(value, dict):
        if set(value) == {REFERENCE}:
            digest = value[REFERENCE]
            if digest in active:
                raise ValueError('Cyclic journal evidence reference')
            raw = fetch(digest)
            if raw is None or sha256(raw.encode('utf-8')).hexdigest() != digest:
                raise ValueError(f'Missing or corrupt journal evidence: {digest}')
            return decode_evidence(json.loads(raw), fetch, active | {digest})
        return {k: decode_evidence(v, fetch, active) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_evidence(v, fetch, active) for v in value]
    return value


def activity_payload(value):
    """Keep decision scalars and selected evidence; hydrate full detail on demand.

    This is explicitly a presentation projection, never a recovery payload.
    Canonical source evidence is preserved separately without truncation.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in EVIDENCE_KEYS:
                if isinstance(item, dict):
                    result[key] = {k: v for k, v in item.items()
                                   if v is None or isinstance(v, (str, int, float, bool))}
                    for selected in ('selected_target_prices', 'profit_targets'):
                        if selected in item:
                            result[key][selected] = item[selected]
                    for selected in ('prior_snapshot_levels', 'qualified_levels'):
                        if isinstance(item.get(selected), (list, tuple)):
                            result[key][selected] = [
                                {k: v for k, v in row.items() if k in CHART_LEVEL_FIELDS}
                                for row in item[selected] if isinstance(row, dict)]
                    if isinstance(item.get('level'), dict):
                        result[key]['level'] = {k: v for k, v in item['level'].items() if k in CHART_LEVEL_FIELDS}
                    snapshot = item.get('current_snapshot')
                    if isinstance(snapshot, dict):
                        result[key]['current_snapshot'] = {
                            k: v for k, v in snapshot.items()
                            if v is None or isinstance(v, (str, int, float, bool))}
                        result[key]['current_snapshot']['levels'] = [
                            {k: v for k, v in row.items() if k in CHART_LEVEL_FIELDS}
                            for row in snapshot.get('levels', []) if isinstance(row, dict)]
                continue
            if key == 'entry_rules' and isinstance(item, dict):
                result[key] = {phase: {k: v for k, v in stage.items()
                                      if k in ('group_scores', 'groups', 'matched_groups', 'operator', 'passed', 'score')}
                               for phase, stage in item.items() if isinstance(stage, dict)}
                continue
            if key == 'order' and isinstance(item, dict):
                result[key] = {k: v for k, v in item.items() if k != 'canonical_metadata'}
                continue
            if key in ('canonical_metadata', 'source_values', 'parameters'):
                continue
            result[key] = activity_payload(item)
        return result
    if isinstance(value, (list, tuple)):
        return [activity_payload(v) for v in value]
    return value
