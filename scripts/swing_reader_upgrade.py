"""Explicit legacy identity bridge, guarded by live reader/checkpoint parity."""
import json
from math import prod
from hashlib import sha256

from src.backend.swing_book_source import read_session as legacy_read, session_bounds, HISTORICAL_POLICY
from src.backend.swing_book_indexed_source import read_session as indexed_read, READER_VERSION
from src.market_engine.swing_book_v6 import StreamingSwingBookV6, VERSION

# Exact pre-upgrade builder bytes (CRLF and LF). Every other identity input
# must still match; this is not permission to accept arbitrary source changes.
LEGACY_BUILDERS = (
    '04e4895f5b928e53abd7716fd67d2979087123fea02cc5e69d3ec45d4152ecc5',
    '139e61ebb6447f418c52a2c6b68b1cb611c8793298a91919d29b393a13ee0671',
    '2afee112036c500021b78de743eb6374044b5fb4e3f1c23d8e284472aa263003',
)
LEGACY_CONTROLLERS = (
    '79cb1d19ed4f07a02ac4e09d08d1a95c25ba91856a4e0ffccb564c84bc6fc1c9',
    'e6ed95e374cdaa709431af546daabd88adc01ffc006dc0776e4a244db55110e2',
    '883be0cc9a43aa2087ff6f655b317a93dc7b33fc60d2b1e015d5524257b50e6b',
    '7993266343d422a845743c49d1a733a7d48b79d163446cdf5596c7eb5a920e02',
)
UPGRADE_PATHS = (
    'src/backend/swing_book_indexed_source.py',
    'scripts/swing_reader_upgrade.py',
    'scripts/prototype_structure_book_clickhouse.py',
)


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def legacy_hash_matches(value, hashes, *, campaign=False):
    baseline = {k:v for k,v in hashes.items() if k.replace('\\','/') not in UPGRADE_PATHS}
    builder_key = next((k for k in baseline if k.replace('\\','/') == 'scripts/build_swing_structure_book.py'), None)
    if builder_key is None:
        return False
    for builder in LEGACY_BUILDERS:
        baseline[builder_key] = builder
        for controller in LEGACY_CONTROLLERS if campaign else (None,):
            if campaign:
                baseline['scripts/build_swing_book_campaign.py'] = controller
            if digest(baseline) == value:
                return True
    return False


def build_identity(hashes, previous, *, indexed):
    current = digest(hashes)
    prior = previous.get('code_hash')
    if prior is None or prior == current:
        return current
    if indexed and legacy_hash_matches(prior,hashes):
        # Retain the database/source fingerprint. The caller MUST run verify()
        # before any new book/session writes, even on subsequent resumptions.
        return prior
    raise ValueError('Unsupported build code change; preserve the existing runtime')


def checkpoint(client, db, marker):
    rows = client.query(f"SELECT state_json FROM {db}.book FINAL WHERE valid_from_us={int(float(marker['closed_at'])*1000000)} ORDER BY level_id",'upgrade_checkpoint')
    state = dict(version=VERSION,closed_at=float(marker['closed_at']),sequence=int(marker['sequence']),
        levels=[json.loads(r['state_json']) for r in rows])
    if digest(state) != marker['state_hash']:
        raise ValueError('Reader upgrade found a corrupt saved checkpoint')
    return state


def verify(ticker, days, done, client, db, splits, *, stop_file=None):
    """Bounded per-ticker gate; no book writes. Repeated on every indexed resume.

    Compare the last certified session and the next unfinished session with
    the unchanged reference SQL, then reproduce the certified closing state.
    The normal builder subsequently verifies every completed prefix marker.
    """
    sessions = [d['source_date'] for d in days]
    if set(done) != set(sessions[:len(done)]):
        raise ValueError('Completed sessions are not a certified contiguous prefix')
    indices = sorted({i for i in (len(done)-1,len(done)) if 0 <= i < len(sessions)})
    evidence = []
    for index in indices:
        if stop_file is not None and stop_file.exists():
            raise KeyboardInterrupt('Stopped before reader verification session')
        session = sessions[index]
        print(f'{ticker} {session} | verifying indexed reader against reference',flush=True)
        original, old_revision = legacy_read(ticker,session,client,policy=HISTORICAL_POLICY,query_workers=1)
        if stop_file is not None and stop_file.exists():
            raise KeyboardInterrupt('Stopped after reference reader verification')
        optimized, revision = indexed_read(ticker,session,client)
        if original != optimized or old_revision != revision:
            raise ValueError(f'Indexed reader parity failed: {ticker} {session}')
        item = dict(session=session,bars=len(optimized),bars_hash=digest(optimized),source_revision=revision)
        if session in done:
            marker = done[session]
            if json.loads(marker['source_revision']) != revision:
                raise ValueError('Certified checkpoint source revision changed')
            seed = checkpoint(client,db,done[sessions[index-1]]) if index else None
            opening,closing = session_bounds(session)
            factor = prod(float(s['split_from'])/float(s['split_to']) for s in splits
                if seed is not None and seed['closed_at'] < session_bounds(s['execution_date'])[0].timestamp() <= opening.timestamp())
            engine = StreamingSwingBookV6(seed,opening.timestamp(),factor)
            for bar in optimized:
                engine.observe(*bar)
            state = engine.closing_state(closing.timestamp())
            checkpoint(client,db,marker)
            if digest(state) != marker['state_hash']:
                raise ValueError('Indexed reader did not reproduce certified closing state')
            item['state_hash'] = marker['state_hash']
        evidence.append(item)
    return dict(status='passed',reader=READER_VERSION,sessions=evidence,
        scope='last_certified_and_next_unfinished_session')
