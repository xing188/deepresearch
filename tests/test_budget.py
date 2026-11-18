import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import research_store as store
from research_budget import BudgetGuard, BudgetExceeded
from research_runtime import CompatibleChatModel


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = patch.object(store, "DB_FILE", Path(self.temp.name) / "budget.sqlite3")
        self.db.start()
        store.initialize()
        self.requests = 0

        async def handle(request):
            self.requests += 1
            await asyncio.sleep(.01)
            return httpx.Response(200, json={
                "id": "test", "object": "chat.completion", "created": 1, "model": "test-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.model = CompatibleChatModel(model="test-model", api_key="test", base_url="https://mock.test/v1", http_async_client=self.client, max_retries=0, use_responses_api=False, stream_usage=False)

    def tearDown(self):
        asyncio.run(self.client.aclose())
        self.db.stop()
        self.temp.cleanup()

    def guard(self, **limits):
        run = store.create("Budget test", budget={"max_seconds": 60, "max_model_calls": 3, "max_tokens": 10000, **limits})
        store.update(run["id"], phase_started_at=None, elapsed_seconds=0)
        return BudgetGuard(run["id"])

    def test_call_limit_cannot_be_bypassed_by_swallowed_errors(self):
        guard = self.guard()

        async def graph_with_retries():
            for _ in range(20):
                try:
                    await self.model.ainvoke("A question")
                except BudgetExceeded:
                    # The upstream graph catches some tool/LLM failures. The
                    # guard still blocks the request and cancels the graph.
                    pass
                await asyncio.sleep(0)

        with self.assertRaises(BudgetExceeded):
            asyncio.run(guard.execute(graph_with_retries))
        self.assertEqual(self.requests, 3)
        self.assertEqual(guard.usage["model_calls"], 3)
        self.assertEqual(guard.usage["in_flight"], 0)

    def test_concurrent_calls_cannot_exceed_limit(self):
        guard = self.guard()

        async def concurrent():
            await asyncio.gather(*(self.model.ainvoke("A question") for _ in range(12)))

        with self.assertRaises(BudgetExceeded):
            asyncio.run(guard.execute(concurrent))
        self.assertEqual(guard.usage["model_calls"], 3)
        self.assertLessEqual(self.requests, 3)
        self.assertEqual(guard.usage["in_flight"], 0)

    def test_token_limit_stops_before_next_call(self):
        guard = self.guard(max_tokens=20)

        async def operation():
            for _ in range(10):
                await self.model.ainvoke("A question")

        with self.assertRaises(BudgetExceeded):
            asyncio.run(guard.execute(operation))
        self.assertEqual(self.requests, 2)
        self.assertEqual(guard.usage["input_tokens"] + guard.usage["output_tokens"], 30)
        self.assertIn("token", guard.reason)

    def test_deadline_cancels_pending_work(self):
        guard = self.guard(max_seconds=.03)
        stopped = []

        async def slow():
            try:
                await asyncio.sleep(10)
            finally:
                stopped.append(True)

        with self.assertRaises(BudgetExceeded):
            asyncio.run(guard.execute(slow))
        self.assertTrue(stopped)
        self.assertEqual(self.requests, 0)

    def test_usage_is_cumulative_across_plan_and_execution(self):
        guard = self.guard()

        async def operation():
            return await self.model.ainvoke("A question")

        asyncio.run(guard.execute(operation))
        next_phase = BudgetGuard(guard.run_id)
        self.assertEqual(next_phase.usage["model_calls"], 1)
        self.assertEqual(next_phase.usage["input_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
