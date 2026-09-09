from threading import Lock
from time import sleep
from unittest.mock import patch

import pytest

from src.backend import swing_book_source as source


class ObservedClient:
    def __init__(self):
        self.lock = Lock()
        self.active = self.peak = self.calls = 0

    def query(self, sql, label):
        with self.lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
        try:
            sleep(.01)
            return [dict(t=float(sql), high=3, low=1, close=2)]
        finally:
            with self.lock:
                self.active -= 1


def read(workers):
    client = ObservedClient()
    with patch.object(source, 'source_metadata', return_value=({'token': 'fixed'}, [])), patch.object(
        source, 'bar_sql', side_effect=lambda ticker, session, left, right, rules, **kw: str(left.timestamp())
    ):
        result = source.read_session('TEST', '2025-01-02', client, query_workers=workers)
    return result, client


def test_serial_budget_preserves_all_chunks_and_order():
    serial, client = read(1)
    parallel, parallel_client = read(4)
    assert serial == parallel
    assert len(serial[0]) == client.calls == parallel_client.calls == 8
    assert client.peak == 1
    assert parallel_client.peak <= 4


@pytest.mark.parametrize('workers', [0, 5, -1, 1.5, True])
def test_invalid_query_budget_fails_before_reading(workers):
    with patch.object(source, 'source_metadata') as metadata:
        with pytest.raises(ValueError, match='concurrent session queries'):
            source.read_session('TEST', '2025-01-02', query_workers=workers)
        metadata.assert_not_called()
