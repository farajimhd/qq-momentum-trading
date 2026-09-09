import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import prototype_structure_book_clickhouse as storage


def test_reader_conflict_retries_without_truncating_published_file(tmp_path):
    target = tmp_path / 'manifest.json'
    target.write_text('{"old": true}')
    replace = Path.replace
    calls = []

    def contested(source, destination):
        calls.append(source)
        assert json.loads(target.read_text()) == {'old': True}
        if len(calls) < 3:
            raise PermissionError('reader holds destination')
        return replace(source, destination)

    with patch.object(Path, 'replace', contested), patch.object(storage.time, 'sleep'):
        storage.save(target, {'new': True})
    assert len(calls) == 3
    assert json.loads(target.read_text()) == {'new': True}
    assert list(tmp_path.iterdir()) == [target]


def test_permanent_denial_preserves_previous_manifest(tmp_path):
    target = tmp_path / 'manifest.json'
    target.write_text('{"old": true}')
    with patch.object(Path, 'replace', side_effect=PermissionError('denied')), patch.object(
        storage.time, 'monotonic', side_effect=[0, 6]
    ):
        with pytest.raises(PermissionError):
            storage.save(target, {'new': True})
    assert json.loads(target.read_text()) == {'old': True}
    assert list(tmp_path.iterdir()) == [target]
