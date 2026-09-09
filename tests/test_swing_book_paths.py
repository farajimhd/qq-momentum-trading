from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.swing_book_paths import WORKSTATION_ENV_FILE, validate_runtime_root
from src.runtime_paths import LAPTOP_RUNTIME_ROOT, WORKSTATION_RUNTIME_ROOT


@pytest.mark.parametrize('root', [LAPTOP_RUNTIME_ROOT, WORKSTATION_RUNTIME_ROOT])
def test_documented_destination_is_preserved(root):
    destination = root / 'structure-validation' / 'campaign'
    with patch.object(Path, 'resolve', lambda path: path), patch.object(Path, 'is_dir', return_value=True):
        assert validate_runtime_root(destination) == destination


def test_unavailable_workstation_does_not_fall_back_to_laptop():
    with patch.object(Path, 'resolve', lambda path: path), patch.object(
        Path, 'is_dir', lambda path: path == LAPTOP_RUNTIME_ROOT
    ):
        with pytest.raises(ValueError, match='unavailable'):
            validate_runtime_root(WORKSTATION_RUNTIME_ROOT / 'campaign')


def test_neighbor_of_runtime_root_is_rejected():
    with patch.object(Path, 'resolve', lambda path: path):
        with pytest.raises(ValueError, match='Runtime must be under'):
            validate_runtime_root(WORKSTATION_RUNTIME_ROOT.parent / 'runtimes-other')


def test_secrets_default_uses_workstation_authority():
    assert WORKSTATION_ENV_FILE == Path(
        r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'
    )
