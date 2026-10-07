import importlib.util
import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch


def load():
    spec = importlib.util.spec_from_file_location("gateway", pathlib.Path(__file__).parents[1] / "llm-gateway.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.g = load()
        self.g.GROQ_KEY = self.g.OR_KEY = "test"

    def test_network_failure_skips_provider_and_fails_over(self):
        reply = b'{"choices":[{"message":{"content":"ok"}}]}'
        with patch.object(self.g, "curl", side_effect=[(0, b""), (200, reply)]) as call:
            result = self.g.try_all({}, [("groq", "a"), ("groq", "b"), ("or", "c")])
        self.assertEqual(result, (200, reply, "c"))
        self.assertEqual(call.call_count, 2)

    def test_deadline_prevents_upstream_request(self):
        with patch.object(self.g, "curl") as call:
            result = self.g.try_all({}, [("groq", "a")], deadline=self.g.time.monotonic() - 1)
        self.assertEqual(result[0], 503)
        call.assert_not_called()

    def test_failed_discovery_keeps_good_pool_and_caches_failure(self):
        self.g._cache["or_vis"] = ["vision"]
        with patch.object(self.g, "curl", return_value=(403, b"denied")) as call:
            self.g.refresh()
            self.g.refresh()
        self.assertEqual(self.g._cache["or_vis"], ["vision"])
        self.assertEqual(call.call_count, 2)

    def test_discovery_filters_active_text_and_free_vision(self):
        groq = b'{"data":[{"id":"new","active":true,"input_modalities":["image","text"]},{"id":"old","active":false},{"id":"whisper"}]}'
        router = b'{"data":[{"id":"free","pricing":{"prompt":"0","completion":"0"},"architecture":{"input_modalities":["image"],"output_modalities":["text"]}},{"id":"paid","pricing":{"prompt":"1","completion":"1"}}]}'
        with patch.object(self.g, "curl", side_effect=[(200, groq), (200, router)]):
            self.g.refresh()
        self.assertEqual(self.g._cache["groq_text"], ["new"])
        self.assertEqual(self.g._cache["groq_vis"], ["new"])
        self.assertEqual(self.g._cache["or_vis"], ["free"])

    def test_provider_pools_alternate_for_bounded_attempts(self):
        self.g._cache.update(groq_text=["a", "b", "c"], or_text=["d", "e"])
        with patch.object(self.g, "refresh"):
            self.assertEqual(self.g.candidates(False)[:3], [("groq", "a"), ("or", "d"), ("groq", "b")])

    def test_responses_image_is_preserved(self):
        chat = self.g.responses_to_chat({"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,test"}]}]})
        self.assertTrue(self.g.msgs_have_image(chat["messages"]))

    def test_recovery_requires_both_failures_budget_and_cooldown(self):
        self.g._provider_failures.update({"groq": 0, "or": 403})
        self.g._last_recovery = self.g.time.time()
        with patch.object(self.g.subprocess, 'run') as run:
            self.assertFalse(self.g.recover_proxy(self.g.time.monotonic() + 45))
            self.g._last_recovery = 0
            self.assertFalse(self.g.recover_proxy(self.g.time.monotonic() + 25))
            self.g._provider_failures['or'] = 401
            self.assertFalse(self.g.recover_proxy(self.g.time.monotonic() + 45))
        run.assert_not_called()

    def test_recovery_refreshes_once_and_persists_cooldown(self):
        self.g._provider_failures.update({"groq": 0, "or": 403})
        self.g._last_recovery = 0
        with tempfile.TemporaryDirectory() as temp:
            self.g.RECOVERY_STATE = str(pathlib.Path(temp) / 'last-refresh')
            with patch.object(self.g.os.path, 'isfile', return_value=True):
                with patch.object(self.g.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)) as run:
                    self.assertTrue(self.g.recover_proxy(self.g.time.monotonic() + 45))
                    self.g._provider_failures.update({"groq": 0, "or": 403})
                    self.assertFalse(self.g.recover_proxy(self.g.time.monotonic() + 45))
            self.assertTrue(pathlib.Path(self.g.RECOVERY_STATE).exists())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs['timeout'], 22)


if __name__ == "__main__":
    unittest.main()
