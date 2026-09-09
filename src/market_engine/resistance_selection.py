"""Read-only resistance-area selection. Scores are evidence grades, not probabilities."""
from hashlib import sha256
from math import isfinite


def select_areas(levels, now, *, maximum_width_bps=100., minimum_score=30., side='resistance', role_safe=False):
    if side not in ('support','resistance') or not isfinite(now) or not isfinite(maximum_width_bps) or maximum_width_bps<=0 or not isfinite(minimum_score) or not 0<=minimum_score<=100:
        raise ValueError('Invalid level selection parameters')
    # Bound the ENTIRE area, not each adjacent gap: prevents transitive chaining.
    candidates = sorted((r for r in levels if r['side'] == side
        and r['state'] == 'active' and r['scale'] == 'major'
        and max(r['pivot_at'], r['confirmed_at'], r.get('formed_at') or 0,
                r.get('last_role_change_at') or 0) <= now),
        key=lambda r: (r['lower'], r['level_id']))
    groups = []
    for row in candidates:
        if not all(isfinite(row[k]) for k in ('price', 'lower', 'upper')) or not 0<row['lower']<=row['price']<=row['upper']:
            raise ValueError('Invalid level geometry')
        if not groups or (max(row['upper'], groups[-1]['upper'])-groups[-1]['lower']) / groups[-1]['lower'] * 10000 > maximum_width_bps:
            groups.append(dict(lower=row['lower'], upper=row['upper'], members=[row]))
        else:
            groups[-1]['members'].append(row)
            groups[-1]['upper'] = max(groups[-1]['upper'], row['upper'])
    result = []
    for group in groups:
        evidence = []
        for row in group['members']:
            retests = max(0, row.get('independent_retests', 0))
            role_tests = max(0, row.get('role_retests', 0))
            flipped = row.get('last_role_change_at') is not None
            threshold = row.get('history_threshold', 0)
            departure = min(1., row.get('best_departure', 0)/threshold) if threshold > 0 else 0.
            if role_safe and flipped:
                # V4's lifetime maximum does not identify the reacting role.
                # Only current-role retests can qualify a flipped candidate.
                departure = 0.
            # Historical support reactions cannot certify a newly flipped resistance.
            tests = role_tests if flipped else retests
            score = 40*departure + 40*min(tests/3, 1) + 20*min(role_tests/2, 1)
            crossings = max(0, row.get('accepted_crossings', 0))
            score = max(0., score-20*crossings)
            if flipped and not role_tests:
                score = min(score, 20.)
            evidence.append((score, row, tests, departure, crossings))
        # Do not add correlated member scores or count their shared encounter repeatedly.
        score, representative, tests, departure, crossings = max(evidence, key=lambda x: (x[0], x[1]['price']))
        ids = sorted(str(r['level_id']) for r in group['members'])
        width_ok = (group['upper']-group['lower'])/group['lower']*10000<=maximum_width_bps
        result.append(dict(id=sha256('|'.join(ids).encode()).hexdigest()[:16],
            representative_id=representative['level_id'],
            price=representative['price'], lower=group['lower'], upper=group['upper'],
            score=round(score, 1), selected=score >= minimum_score and (width_ok or not role_safe), members=ids,
            reasons=[f'{len(ids)} candidates; strongest evidence used, not summed',
                     f'{tests} independent/current-role retests',
                     f'departure qualification {departure:.0%}',
                     f'{crossings} accepted crossings',
                     f'new {side} role awaiting validation' if representative.get('last_role_change_at') is not None and not representative.get('role_retests') else f'{side} evidence available',
                     *(['individual band exceeds width budget'] if role_safe and not width_ok else [])]))
    return result
