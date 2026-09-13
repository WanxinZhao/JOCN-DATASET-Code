import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.planner_agent import PlannerAgent
from utils import llm
from utils.llm_audit import finish_llm_trace, start_llm_trace


class LlmAuditTest(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "openai",
                "LLM_MAX_RETRIES": "2",
                "LLM_STRICT_MODE": "1",
                "OPENAI_MODEL": "gpt-4o-mini",
                "OPENAI_PRICING_SNAPSHOT_DATE": "2026-09-12",
                "OPENAI_INPUT_USD_PER_1M_TOKENS": "0.15",
                "OPENAI_CACHED_INPUT_USD_PER_1M_TOKENS": "0.075",
                "OPENAI_OUTPUT_USD_PER_1M_TOKENS": "0.60",
                "OPENAI_PRICING_SOURCE": "unit-test-fixture",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def _start(self, run_id="test_run") -> Path:
        return start_llm_trace(
            run_id,
            base_dir=self.temporary.name,
            user_request="test request",
        )

    def test_success_persists_exact_request_response_and_usage(self):
        trace_dir = self._start()
        raw_response = {
            "id": "chatcmpl-test",
            "model": "gpt-4o-mini-2024-07-18",
            "system_fingerprint": "fp_test",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"ok": true}'},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 40},
            },
        }
        with patch.object(llm, "_call_openai", return_value=('{"ok": true}', raw_response)):
            result = llm.call_llm("complete runtime prompt", call_name="planner_agent")
        finish_llm_trace("completed")

        self.assertEqual(result, '{"ok": true}')
        request_file = next((trace_dir / "requests").glob("*.json"))
        request = json.loads(request_file.read_text(encoding="utf-8"))
        self.assertEqual(request["payload"]["messages"][1]["content"], "complete runtime prompt")

        response_file = next((trace_dir / "responses").glob("*.json"))
        response = json.loads(response_file.read_text(encoding="utf-8"))
        self.assertEqual(response["payload"]["id"], "chatcmpl-test")

        manifest = json.loads((trace_dir / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["summary"]["api_attempts"], 1)
        self.assertEqual(manifest["summary"]["total_tokens"], 120)
        self.assertIsNotNone(manifest["summary"]["estimated_cost_usd"])

    def test_retry_records_failure_and_success_as_separate_attempts(self):
        trace_dir = self._start("retry_run")
        results = [
            RuntimeError("429 test limit"),
            (
                "done",
                {
                    "id": "chatcmpl-retry",
                    "model": "gpt-4o-mini",
                    "choices": [{"finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                },
            ),
        ]
        with patch.object(llm, "_call_openai", side_effect=results), patch.object(
            llm.time, "sleep", return_value=None
        ):
            result = llm.call_llm("retry prompt", call_name="reflection_agent")
        finish_llm_trace("completed")

        self.assertEqual(result, "done")
        records = [
            json.loads(line)
            for line in (trace_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([record["status"] for record in records], ["error", "success"])
        self.assertEqual(len(list((trace_dir / "requests").glob("*.json"))), 2)
        with (trace_dir / "call_summary.csv").open(encoding="utf-8", newline="") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 2)

    def test_strict_mode_does_not_hide_llm_failure_with_rule_fallback(self):
        with patch("orchestrator.planner_agent.call_llm", side_effect=RuntimeError("API failed")):
            with self.assertRaisesRegex(RuntimeError, "API failed"):
                PlannerAgent().plan("generate an NDFF QoT dataset")


if __name__ == "__main__":
    unittest.main()
