import asyncio
import unittest
from unittest.mock import AsyncMock

from src.backend.replay_run_service import ReplayRunController


class ReplayPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_chart_reads_share_build_and_survive_reader_cancellation(self):
        controller = object.__new__(ReplayRunController)
        started, release = asyncio.Event(), asyncio.Event()

        async def build(symbol):
            started.set()
            await release.wait()
            return {'symbol': symbol, 'presentation_as_of': 'frozen-clock'}

        controller._build_canvas_payload = AsyncMock(side_effect=build)
        first = asyncio.create_task(controller.canvas_payload('aapl'))
        await started.wait()
        second = asyncio.create_task(controller.canvas_payload('AAPL'))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertEqual((await second)['presentation_as_of'], 'frozen-clock')
        self.assertEqual(controller._build_canvas_payload.await_count, 1)
        await asyncio.sleep(0)
        self.assertEqual(controller._canvas_publications, {})
        await controller.canvas_payload('AAPL')
        self.assertEqual(controller._build_canvas_payload.await_count, 2)

    async def test_failed_publication_does_not_poison_next_read(self):
        controller = object.__new__(ReplayRunController)
        controller._build_canvas_payload = AsyncMock(side_effect=[ValueError('source failed'), {'ok': True}])
        with self.assertRaisesRegex(ValueError, 'source failed'):
            await controller.canvas_payload('AAPL')
        self.assertEqual(await controller.canvas_payload('AAPL'), {'ok': True})
