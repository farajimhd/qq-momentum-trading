"""Explicit operational destinations for historical swing-book builders."""
from pathlib import Path

from src.runtime_paths import LAPTOP_RUNTIME_ROOT, WORKSTATION_RUNTIME_ROOT

WORKSTATION_ENV_FILE = WORKSTATION_RUNTIME_ROOT.parent / 'secrets' / '.env'


def validate_runtime_root(destination: Path) -> Path:
    """Accept only documented roots; never redirect an unavailable destination."""
    resolved = destination.resolve()
    for authority in (WORKSTATION_RUNTIME_ROOT, LAPTOP_RUNTIME_ROOT):
        if resolved.is_relative_to(authority.resolve()):
            if not authority.is_dir():
                raise ValueError(f'Required runtime root unavailable: {authority}')
            return resolved
    raise ValueError(
        f'Runtime must be under {WORKSTATION_RUNTIME_ROOT} or {LAPTOP_RUNTIME_ROOT}'
    )
