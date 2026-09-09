"""Versioned JSON checkpoints for the shared detector; no executable payloads."""
from collections import Counter, deque
from dataclasses import asdict, is_dataclass
from math import isfinite

from .structural_detector import StructuralDetector, DetectorSettings, VERSION
from .structural_evidence import Interactions
from .structural_progression import Progression
from .structural_volume import VolumeLevels
from .swing_structure import SwingStructure, SwingSettings

TYPES = {c.__name__: c for c in (StructuralDetector, DetectorSettings, Interactions,
    Progression, VolumeLevels, SwingStructure, SwingSettings)}


def encode(value):
    if isinstance(value, Counter):
        return {'type': 'counter', 'items': encode(dict(value))}
    if isinstance(value, dict):
        return {'type': 'dict', 'items': [[encode(k), encode(v)] for k,v in value.items()]}
    if isinstance(value, deque):
        return {'type': 'deque', 'maxlen': value.maxlen, 'items': [encode(v) for v in value]}
    if isinstance(value, (tuple, set)):
        return {'type': type(value).__name__, 'items': [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if type(value).__name__ in TYPES:
        return {'type': type(value).__name__, 'state': encode(asdict(value) if is_dataclass(value) else vars(value))}
    if isinstance(value, float) and not isfinite(value):
        if value == float('-inf'):
            return {'type': 'negative_infinity'}
        raise ValueError('Invalid detector checkpoint number')
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError('Unsupported detector checkpoint value')


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    kind = value['type']
    if kind == 'dict':
        return {decode(k): decode(v) for k,v in value['items']}
    if kind == 'counter':
        return Counter(decode(value['items']))
    if kind == 'deque':
        return deque((decode(v) for v in value['items']), maxlen=value['maxlen'])
    if kind in ('tuple', 'set'):
        return (tuple if kind == 'tuple' else set)(decode(v) for v in value['items'])
    if kind == 'negative_infinity':
        return float('-inf')
    cls = TYPES[kind]
    state = decode(value['state'])
    if cls in (DetectorSettings, SwingSettings):
        return cls(**state)
    instance = cls.__new__(cls)
    instance.__dict__.update(state)
    return instance


def checkpoint(detector):
    return {'contract': VERSION, 'state': encode(detector)}


def restore(value):
    if value.get('contract') != VERSION:
        raise ValueError('Detector checkpoint version mismatch')
    result = decode(value['state'])
    if type(result) is not StructuralDetector:
        raise ValueError('Invalid detector checkpoint root')
    return result
