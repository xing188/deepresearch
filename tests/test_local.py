from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from auth_client import TestClient

import local_config
import research_runtime as runtime
import research_store as store
import server


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(store, "DB_FILE", Path(self.temp.name) / "test.sqlite3")
        self.env_patch = patch.object(local_config, "ENV_FILE", Path(self.temp.name) / ".env")
        self.db_patch.start()
        self.env_patch.start()
        store.initialize()

    def tearDown(self):
        self.env_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_settings_and_routes(self):
        with TestClient(server.app) as client:
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/static/app.js").status_code, 200)
            self.assertEqual(client.get("/api/health").json()["upstream"], "langchain-ai/open_deep_research")
            self.assertEqual(client.post("/api/runs", json={"question": "A question"}).status_code, 400)
            payload = {"base_url": "https://example.test/v1", "model": "chat-model", "api_key": "secret${PATH}#quotes'", "search_key": "tvly-test-secret"}
            response = client.put("/api/settings", json=payload)
            self.assertTrue(response.json()["ready"])
            self.assertNotIn(payload["api_key"], response.text)
            self.assertNotIn(payload["search_key"], client.get("/api/settings").text)
            self.assertEqual(local_config.settings()["api_key"], payload["api_key"])
            payload.update(api_key="", search_key="")
            client.put("/api/settings", json=payload)
            self.assertEqual(local_config.settings()["api_key"], "secret${PATH}#quotes'")
            self.assertEqual(client.put("/api/settings", json=payload, headers={"Origin": "https://evil.test"}).status_code, 403)
            payload["base_url"] = "javascript:alert(1)"
            self.assertEqual(client.put("/api/settings", json=payload).status_code, 400)

    def test_report_rendering_and_restart(self):
        run = store.create("A question")
        store.update(run["id"], report="# Report\n<script>alert(1)</script>\n[bad](javascript:alert(1))\n[good](https://example.com)", status="complete")
        with TestClient(server.app) as client:
            result = client.get(f"/api/runs/{run['id']}").json()
            self.assertNotIn("<script>", result["report_html"])
            self.assertNotIn('href="javascript:', result["report_html"])
            self.assertIn("https://example.com", result["report_html"])
            download = client.get(f"/api/runs/{run['id']}/report")
            self.assertEqual(download.status_code, 200)
            self.assertIn("attachment", download.headers["content-disposition"])
        interrupted = store.create("Interrupted question")
        store.initialize()
        self.assertEqual(store.get(interrupted["id"])["status"], "interrupted")

    def test_cancellation(self):
        local_config.save_settings({"api_key": "test-model-key", "search_key": "test-search-key"})

        async def wait_forever(run_id, *args):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                store.update(run_id, status="cancelled")
                raise

        with patch.object(runtime, "generate_plan", wait_forever), TestClient(server.app) as client:
            run = client.post("/api/runs", json={"question": "Question to cancel"}).json()
            self.assertEqual(client.post("/api/runs", json={"question": "Second question"}).status_code, 409)
            result = client.post(f"/api/runs/{run['id']}/cancel", json={})
            self.assertEqual(result.json()["status"], "cancelled")

    def test_official_graph_with_mock_services(self):
        calls = []
        fail_writer = [False]

        def handle(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "compatible-model")
            names = [tool["function"]["name"] for tool in payload.get("tools", [])]
            calls.append(names)
            tool_name, args, content = None, None, None
            if "ResearchPlan" in names:
                tool_name, args = "ResearchPlan", {"objective": "Research public evidence", "outline": ["Evidence", "Limitations"], "focus": "Public sources", "exclusions": "Unrelated products", "source_preferences": "Primary research"}
            elif "ConductResearch" in names:
                done = any(msg.get("role") == "tool" and msg.get("name") == "ConductResearch" for msg in payload["messages"])
                # Providers may omit the name on tool messages; match the earlier call.
                done = done or any(msg.get("role") == "tool" for msg in payload["messages"])
                tool_name = "ResearchComplete" if done else "ConductResearch"
                args = {} if done else {"research_topic": "Research the question using public evidence."}
            elif "Summary" in names:
                tool_name, args = "Summary", {"summary": "Evidence summary", "key_excerpts": "Verifiable statement"}
            elif "tavily_search" in names:
                done = any(msg.get("role") == "tool" for msg in payload["messages"])
                tool_name = "ResearchComplete" if done else "tavily_search"
                args = {} if done else {"queries": ["test evidence"]}
            else:
                if fail_writer[0] and '<Research Brief>' in str(payload['messages']):
                    return httpx.Response(503, json={'error': {'message': 'temporary writer unavailable'}})
                content = "# Evidence report\n\nFinding supported by [Example](https://example.com/evidence)."
            message = {"role": "assistant", "content": content}
            if tool_name:
                message["tool_calls"] = [{"id": f"call_{len(calls)}", "type": "function", "function": {"name": tool_name, "arguments": json.dumps(args)}}]
            return httpx.Response(200, json={"id": f"mock_{len(calls)}", "object": "chat.completion", "created": 1, "model": "compatible-model", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_name else "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

        class Search:
            def __init__(self, **kwargs):
                pass

            async def search(self, query, **kwargs):
                return {"query": query, "results": [{"title": "Example evidence", "url": "https://example.com/evidence", "content": "Evidence", "raw_content": "Public evidence text."}]}

        real_model = runtime.CompatibleChatModel

        class MockModel(real_model):
            def __init__(self, **kwargs):
                kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(handle))
                kwargs["http_async_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handle))
                super().__init__(**kwargs)

        run = store.create("Test official research graph", status="planning")
        current = {"base_url": "https://mock.test/v1", "model": "compatible-model", "api_key": "mock-model-key", "search_key": "mock-search-key"}
        with patch.object(runtime, "CompatibleChatModel", MockModel), patch.object(runtime.utils, "AsyncTavilyClient", Search):
            asyncio.run(runtime.generate_plan(run["id"], run["question"], current, "quick"))
            waiting = store.get(run["id"])
            self.assertEqual(waiting["status"], "awaiting_confirmation", waiting["error"])
            self.assertEqual(waiting["sources"], [])
            self.assertEqual(waiting["usage"]["model_calls"], 1)
            self.assertIsNone(waiting["phase_started_at"])
            self.assertEqual(len(calls), 1, "Planning must not search")
            approved = waiting["plan"]
            approved["objective"] = "Only compare verified evidence"
            store.begin_phase(run["id"], "running", plan=approved)
            asyncio.run(runtime.run_research(run["id"], run["question"], current, "quick"))
        result = store.get(run["id"])
        self.assertEqual(result["status"], "complete", result["error"])
        self.assertEqual(len(result["sources"]), 1)
        self.assertIn("Evidence report", result["report"])
        self.assertGreater(result["usage"]["model_calls"], 4)
        self.assertTrue(any("ResearchPlan" in names for names in calls))
        self.assertFalse(any("ResearchQuestion" in names for names in calls), "Approved plan must not be replanned")
        self.assertTrue(any("tavily_search" in names for names in calls))
        self.assertEqual(result['sources'][0]['excerpt'], 'Public evidence text.')
        self.assertTrue(result['sources'][0]['retrieved_at'])

        # Simulate loss of the writer after completed research. Reopen the durable
        # saver in a new event loop and verify only the pending writer runs again.
        recover = store.create('Resume without repeating completed research', status='planning')
        with patch.object(runtime, 'CompatibleChatModel', MockModel), patch.object(runtime.utils, 'AsyncTavilyClient', Search):
            asyncio.run(runtime.generate_plan(recover['id'], recover['question'], current, 'quick'))
            store.begin_phase(recover['id'], 'running')
            fail_writer[0] = True
            asyncio.run(runtime.run_research(recover['id'], recover['question'], current, 'quick'))
            stopped = store.get(recover['id'])
            self.assertEqual(stopped['status'], 'failed', stopped['error'])
            self.assertTrue(stopped['checkpointed'])
            self.assertTrue(asyncio.run(runtime.resumable(recover['id'])))
            fail_writer[0] = False
            before_resume = len(calls)
            store.begin_phase(recover['id'], 'running')
            asyncio.run(runtime.run_research(recover['id'], recover['question'], current, 'quick', resume=True))
            self.assertEqual(store.get(recover['id'])['status'], 'complete', store.get(recover['id'])['error'])
            self.assertEqual(len(calls)-before_resume, 1, 'Completed supervisor and searches must not run again')

        # Exercise the same real graph under a small call budget. Search results
        # must survive even when their summarization is blocked by the budget.
        limited = store.create("Budget-limited official graph", status="planning", budget={"max_seconds": 60, "max_model_calls": 3, "max_tokens": 20000})
        before = len(calls)
        with patch.object(runtime, "CompatibleChatModel", MockModel), patch.object(runtime.utils, "AsyncTavilyClient", Search), patch("logging.warning"):
            asyncio.run(runtime.generate_plan(limited["id"], limited["question"], current, "quick"))
            store.begin_phase(limited["id"], "running")
            asyncio.run(runtime.run_research(limited["id"], limited["question"], current, "quick"))
        result = store.get(limited["id"])
        self.assertEqual(result["status"], "budget_exceeded", result["error"])
        self.assertEqual(len(calls) - before, 3)
        self.assertEqual(result["usage"]["model_calls"], 3)
        self.assertEqual(result["usage"]["in_flight"], 0)
        self.assertEqual(len(result["sources"]), 1)
        self.assertFalse(result["report"])
        self.assertIsNone(result["phase_started_at"])

    def test_legacy_migration_and_pending_plan_survive_restart(self):
        import sqlite3
        original = Path(self.temp.name) / "old-schema.sqlite3"
        with sqlite3.connect(original) as db:
            db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, question TEXT, status TEXT, created_at TEXT, updated_at TEXT, report TEXT DEFAULT '', error TEXT DEFAULT '', brief TEXT DEFAULT '', sources TEXT DEFAULT '[]', usage TEXT DEFAULT '{}')")
            db.execute("INSERT INTO runs VALUES ('old','Legacy question','complete',?,?,'Original report','','','[]','{}')", (store.now(), store.now()))
        db.close()
        with patch.object(store, "DB_FILE", original):
            store.initialize()
            self.assertEqual(store.get("old")["report"], "Original report")
            self.assertEqual(store.get("old")["budget"], {})
            waiting = store.create("Waiting plan", status="awaiting_confirmation")
            store.update(waiting["id"], plan={"objective": "Saved objective", "outline": ["Saved section"]})
            store.initialize()
            self.assertEqual(store.get(waiting["id"])["status"], "awaiting_confirmation")
            self.assertEqual(store.get(waiting["id"])["plan"]["objective"], "Saved objective")

    def test_plan_confirmation_is_required_and_edited_plan_is_used(self):
        local_config.save_settings({"api_key": "test-model-key", "search_key": "test-search-key"})
        approved = {"objective": "Specific revised objective", "outline": ["Only scope", "Evidence"], "focus": "Focus on cost", "exclusions": "No unrelated topics", "source_preferences": "Primary sources"}
        received = []

        async def fake_plan(run_id, *args):
            store.finish_phase(run_id, "awaiting_confirmation", plan={**approved, "objective": "Original objective"}, usage={"model_calls": 1, "input_tokens": 20, "output_tokens": 10})

        async def fake_research(run_id, *args):
            received.append(store.get(run_id))
            store.finish_phase(run_id, "complete", report="# Completed")

        import time
        with patch.object(runtime, "generate_plan", fake_plan), patch.object(runtime, "run_research", fake_research), TestClient(server.app) as client:
            response = client.post("/api/runs", json={"question": "A narrow research question", "budget": {"max_seconds": 120, "max_model_calls": 10, "max_tokens": 20000}})
            self.assertEqual(response.status_code, 201)
            run_id = response.json()["id"]
            for _ in range(30):
                waiting = client.get(f"/api/runs/{run_id}").json()
                if waiting["status"] == "awaiting_confirmation":
                    break
                time.sleep(.01)
            self.assertEqual(waiting["status"], "awaiting_confirmation")
            self.assertFalse(received, "Research must not run before approval")
            before = waiting["elapsed_seconds"]
            self.assertEqual(client.get(f"/api/runs/{run_id}").json()["elapsed_seconds"], before)
            response = client.post(f"/api/runs/{run_id}/confirm", json={"plan": approved, "budget": {"max_seconds": 120, "max_model_calls": 10, "max_tokens": 20000}})
            self.assertEqual(response.status_code, 200)
            for _ in range(30):
                if received:
                    break
                time.sleep(.01)
            self.assertEqual(received[0]["plan"], approved)
            self.assertIn("Specific revised objective", received[0]["brief"])
            self.assertEqual(client.post(f"/api/runs/{run_id}/confirm", json={"plan": approved}).status_code, 409)

    def test_budget_validation_and_partial_materials(self):
        with TestClient(server.app) as client:
            for budget in ({"max_seconds": 0}, {"max_model_calls": 201}, {"max_tokens": -1}, {"max_seconds": True}):
                self.assertEqual(client.post("/api/runs", json={"question": "Valid question", "budget": budget}).status_code, 422)
            run = store.create("Stopped research")
            store.finish_phase(run["id"], "budget_exceeded", sources=[{"title": "Source", "url": "https://example.com"}], notes=["Preliminary note"])
            response = client.get(f"/api/runs/{run['id']}/materials")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Preliminary note", response.text)
            self.assertIn("https://example.com", response.text)
            self.assertEqual(client.get(f"/api/runs/{run['id']}/report").status_code, 404)


if __name__ == "__main__":
    unittest.main()
