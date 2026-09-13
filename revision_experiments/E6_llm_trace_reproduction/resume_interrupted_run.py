#!/usr/bin/env python3
"""Resume the interrupted E6 run from its durable agent logs.

This deliberately does not call the planner or scenario-expander again.  It
executes only scenarios with no durable result, recovers the final reflection
call, replays the nine original one-channel failures once with the fixed GNPy
code, and then writes the normal dataset artifacts.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_DIR.parents[1]
PIPELINE_DIR = REPO_ROOT / "llm_pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from orchestrator.execution_agent import ExecutionAgent  # noqa: E402
from orchestrator.reflection_agent import ReflectionAgent  # noqa: E402
from orchestrator.report_agent import ReportAgent  # noqa: E402
from orchestrator.results_analysis_agent import ResultsAnalysisAgent  # noqa: E402


RUN_ID = "20260913_134829_038619"
USER_REQUEST = (
    "Generate a raw QoT dataset for all NDFF paths starting from Bristol using "
    "8 Voyager transmitters only. Use 8 channel slots and enumerate all binary "
    "on/off channel patterns across the 8 slots. Simulate only QPSK and 16QAM."
)
RESUME_LOG_NAME = "resume_execution_agent.log"


def _payload_from_log_line(line: str) -> dict[str, Any] | None:
    start = line.find("{")
    if start < 0:
        return None
    try:
        value = json.loads(line[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _log_payloads(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            payload = _payload_from_log_line(line)
            if payload is not None:
                yield payload


def _load_run(run_dir: Path) -> dict[str, Any]:
    logs = run_dir / "agent_logs"
    scenario_log = next(iter(sorted(logs.glob("*_scenario_expander_agent.log"))), None)
    execution_log = next(iter(sorted(logs.glob("*_execution_agent.log"))), None)
    planner_log = next(iter(sorted(logs.glob("*_planner_agent.log"))), None)
    reflection_log = next(iter(sorted(logs.glob("*_reflection_agent.log"))), None)
    orchestrator_log = next(iter(sorted(logs.glob("*_orchestrator.log"))), None)
    analysis_log = next(iter(sorted(logs.glob("*_results_analysis_agent.log"))), None)
    manifest_path = run_dir / "llm_trace" / "run_manifest.json"
    required = {
        "scenario_expander": scenario_log,
        "execution": execution_log,
        "planner": planner_log,
        "reflection": reflection_log,
        "orchestrator": orchestrator_log,
        "analysis": analysis_log,
        "manifest": manifest_path if manifest_path.exists() else None,
    }
    absent = [name for name, path in required.items() if path is None]
    if absent:
        raise RuntimeError(f"Missing required original run artifacts: {', '.join(absent)}")

    scenarios: list[dict[str, Any]] = []
    for payload in _log_payloads(scenario_log):
        values = payload.get("scenarios")
        if isinstance(values, list):
            scenarios.extend(item for item in values if isinstance(item, dict))
    if not scenarios:
        raise RuntimeError("No generated scenarios recovered from the scenario-expander log.")

    scenario_by_id = {str(item.get("scenario_id")): item for item in scenarios}
    if len(scenario_by_id) != len(scenarios):
        raise RuntimeError("Scenario-expander log contains duplicate scenario IDs.")

    original_outputs: dict[str, dict[str, Any]] = {}
    for payload in _log_payloads(execution_log):
        scenario_id = str(payload.get("scenario_id", ""))
        if scenario_id and scenario_id in scenario_by_id and "output" in payload:
            original_outputs[scenario_id] = payload

    planner_payloads = list(_log_payloads(planner_log))
    iteration2_payload = next(
        (item for item in planner_payloads if item.get("iteration") == 2 and isinstance(item.get("task"), dict)),
        None,
    )
    if iteration2_payload is None:
        raise RuntimeError("Iteration-2 planner task is missing from the saved agent log.")

    reflection_payloads = list(_log_payloads(reflection_log))
    reflection1_payload = next(
        (item for item in reflection_payloads if item.get("iteration") == 1), None
    )
    if reflection1_payload is None:
        raise RuntimeError("Iteration-1 reflection is missing from the saved agent log.")

    path_bundle = next(iter(_log_payloads(orchestrator_log)), None)
    if not isinstance(path_bundle, dict) or not path_bundle.get("paths"):
        raise RuntimeError("Resolved path bundle is missing from the saved orchestrator log.")

    analysis_payload = next(iter(_log_payloads(analysis_log)), None)
    analysis1 = analysis_payload.get("analysis") if isinstance(analysis_payload, dict) else None
    if not isinstance(analysis1, dict):
        raise RuntimeError("Iteration-1 analysis is missing from the saved agent log.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("run_id") != run_dir.name:
        raise RuntimeError("Run directory and trace manifest run IDs differ.")
    return {
        "scenarios": scenarios,
        "scenario_by_id": scenario_by_id,
        "original_outputs": original_outputs,
        "task2": iteration2_payload["task"],
        "reflection1": reflection1_payload.get("reflection", {}),
        "path_bundle": path_bundle,
        "analysis1": analysis1,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "trace_dir": run_dir / "llm_trace",
        "execution_log": execution_log,
    }


def _load_resume_outputs(path: Path, scenario_by_id: dict[str, Any]) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    for payload in _log_payloads(path):
        scenario_id = str(payload.get("scenario_id", ""))
        if scenario_id and scenario_id in scenario_by_id and "output" in payload:
            if payload.get("_resume_action") == "uncaught_not_completed":
                continue
            error = str(payload.get("output", {}).get("metrics", {}).get("error_message", ""))
            # Earlier continuation attempt reached no simulator: local
            # PostgreSQL was unavailable before config construction. Keep its
            # audit line, but do not count it as a completed scenario result.
            if (
                "Uncaught resume execution error: OperationalError:" in error
                and "connection to server at \"localhost\"" in error
                and not payload.get("sim_config")
            ):
                continue
            outputs[scenario_id] = payload
    return outputs


def _as_record(payload: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "tool": payload.get("engine", scenario.get("preferred_engine", "gnpy")),
        "reason": "Scenario Expander selected this engine from the task semantics.",
        "sim_config": payload.get("sim_config", {}),
        "output": payload.get("output", {}),
    }


def _counts(payloads: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: collections.Counter[str] = collections.Counter()
    for item in payloads.values():
        status = str(item.get("output", {}).get("status", "unknown"))
        counts["no_signal" if status == "not_applicable" else status] += 1
    return dict(sorted(counts.items()))


def _jsonl_records(path: Path) -> list[dict[str, Any]]:
    records = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
    return records


def _recover_env_key(env_path: Path) -> str:
    if not env_path.is_file():
        raise RuntimeError(f"Authorized API-key file not found: {env_path}")
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == "OPENAI_API_KEY":
            secret = value.strip().strip('"').strip("'")
            if secret:
                return secret
    raise RuntimeError("OPENAI_API_KEY is absent from the authorized .env file.")


def _apply_manifest_llm_settings(manifest: dict[str, Any], env_path: Path) -> None:
    configuration = manifest.get("llm_configuration", {})
    if configuration.get("provider") != "openai":
        raise RuntimeError("Saved run used a non-OpenAI provider; refusing provider substitution.")
    os.environ["OPENAI_API_KEY"] = _recover_env_key(env_path)
    os.environ["LLM_PROVIDER"] = "openai"
    os.environ.pop("LLM_FALLBACK_PROVIDER", None)
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("OPENAI_BASE_URL", None)
    os.environ["OPENAI_MODEL"] = str(configuration.get("openai_model_requested", "gpt-4o-mini"))
    os.environ["LLM_TIMEOUT_SECONDS"] = str(configuration.get("timeout_seconds", "90"))
    os.environ["LLM_MAX_RETRIES"] = str(configuration.get("maximum_attempts_per_provider", "3"))
    os.environ["LLM_STRICT_MODE"] = str(configuration.get("strict_mode", "1"))
    pricing = configuration.get("pricing", {})
    pricing_env = {
        "OPENAI_PRICING_SNAPSHOT_DATE": "snapshot_date",
        "OPENAI_INPUT_USD_PER_1M_TOKENS": "input",
        "OPENAI_CACHED_INPUT_USD_PER_1M_TOKENS": "cached_input",
        "OPENAI_OUTPUT_USD_PER_1M_TOKENS": "output",
        "OPENAI_PRICING_SOURCE": "source",
    }
    for variable, key in pricing_env.items():
        value = pricing.get(key)
        if value is not None:
            os.environ[variable] = str(value)


def _reflection_from_saved_success(
    trace_dir: Path,
    calls: list[dict[str, Any]],
    analysis: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any] | None:
    successful = [
        item for item in calls
        if item.get("call_name") == "reflection_agent"
        and item.get("status") == "success"
        and int(str(item.get("call_id", "call_000")).split("_")[1]) >= 6
    ]
    if not successful:
        return None
    from utils.llm import parse_llm_json
    from orchestrator.reflection_agent import ReflectionAgent

    call = successful[-1]
    response_rel = call.get("response_file")
    if not response_rel:
        raise RuntimeError("A successful final reflection call has no saved response file.")
    wrapper = json.loads((trace_dir / response_rel).read_text(encoding="utf-8"))
    payload = wrapper.get("payload", {})
    content = payload.get("choices", [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content:
        raise RuntimeError("Saved final reflection response has no parseable assistant content.")
    parsed = parse_llm_json(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("Saved final reflection response is not a JSON object.")
    return ReflectionAgent()._normalize_reflection(parsed, analysis, task)


def _analysis_summary(analysis: dict[str, Any]) -> dict[str, Any]:
    best = analysis.get("best_result") or {}
    best_scenario = best.get("scenario", {})
    best_output = best.get("output", {})
    return {
        key: analysis.get(key)
        for key in (
            "total_scenarios", "ok_scenarios", "error_scenarios",
            "no_signal_scenarios", "invalid_metric_scenarios", "status_counts",
            "engine_counts", "transmitter_counts", "modulation_counts",
            "unique_channel_patterns", "unique_launch_powers_dbm", "power_summary",
        )
    } | {
        "best_score": analysis.get("best_score"),
        "best_scenario_id": best_scenario.get("scenario_id"),
        "best_status": best_output.get("status"),
        "best_metrics": best_output.get("metrics", {}),
    }


def _refresh_completed_trace_manifest(
    run_data: dict[str, Any], resume_log: Path
) -> dict[str, Any]:
    """Keep cumulative resumption metadata correct on idempotent finalization."""
    resume_outputs = _load_resume_outputs(resume_log, run_data["scenario_by_id"])
    executed_missing = [
        scenario_id for scenario_id, payload in resume_outputs.items()
        if payload.get("_resume_action") == "missing_scenario"
        and payload.get("sim_config")
    ]
    recovered_failures = [
        scenario_id for scenario_id, payload in resume_outputs.items()
        if payload.get("_resume_action") == "retry_original_failure"
    ]
    runtime_dirs: collections.Counter[str] = collections.Counter()
    for payload in resume_outputs.values():
        for artifact_path in payload.get("output", {}).get("artifacts", {}).values():
            if isinstance(artifact_path, str):
                path = Path(artifact_path)
                if path.is_file():
                    runtime_dirs[str(path.parent)] += 1
    runtime_dir = Path(runtime_dirs.most_common(1)[0][0]) if runtime_dirs else None
    runtime_started = None
    if runtime_dir is not None:
        try:
            timestamp = runtime_dir.name.rsplit("_", 1)[-1]
            runtime_started = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            ).isoformat()
        except ValueError:
            pass

    calls = _jsonl_records(run_data["trace_dir"] / "calls.jsonl")
    final_reflections = [
        item for item in calls
        if item.get("call_name") == "reflection_agent"
        and item.get("status") == "success"
        and int(str(item.get("call_id", "call_000")).split("_")[1]) >= 6
    ]
    additional_call = final_reflections[-1].get("call_id") if final_reflections else None
    finalized_at = datetime.now(timezone.utc).isoformat()

    manifest_path = run_data["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    resumption = dict(manifest.get("resumption", {}))
    resumption.update(
        {
            "reason": "The user interrupted the running workflow and later requested continuation from saved logs.",
            "resumed_at_utc": runtime_started or resumption.get("resumed_at_utc"),
            "finalized_at_utc": finalized_at,
            "original_successful_llm_calls_repeated": 0,
            "additional_llm_call": additional_call or "final iteration-2 reflection recovered from saved response",
            "missing_scenarios_executed": len(executed_missing),
            "original_single_channel_errors_replayed": sorted(
                recovered_failures,
                key=lambda value: int(value.rsplit("-", 1)[-1]),
            ),
            "recovered_original_failure_count": len(recovered_failures),
            "resume_execution_log": str(resume_log),
            "metadata_refreshed_at_utc": finalized_at,
        }
    )
    if runtime_dir is not None:
        resumption["runtime_directory"] = str(runtime_dir)
    if runtime_started:
        resumption["successful_resume_started_at_utc"] = runtime_started
    manifest["resumption"] = resumption

    if not manifest.get("human_interventions"):
        manifest["human_interventions"] = [
            {
                "type": "interruption_recovery",
                "details": "The user manually interrupted the long-running process and subsequently requested continuation. The exact interruption timestamp was not captured.",
            }
        ]
    run_result = dict(manifest.get("run_result", {}))
    run_result["resumed_missing_scenarios"] = len(executed_missing)
    run_result["retried_original_failures"] = len(recovered_failures)
    manifest["run_result"] = run_result
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "missing_scenarios_executed": len(executed_missing),
        "original_failures_replayed": len(recovered_failures),
        "runtime_directory": str(runtime_dir) if runtime_dir else None,
        "additional_llm_call": additional_call,
    }


def _write_final_artifacts(
    run_dir: Path,
    run_data: dict[str, Any],
    final_records: list[dict[str, Any]],
    analysis_before_recovery: dict[str, Any],
    final_analysis: dict[str, Any],
    reflection2: dict[str, Any],
    recovered_ids: list[str],
    initial_counts: dict[str, int],
    pre_recovery_counts: dict[str, int],
    resume_log: Path,
) -> dict[str, str]:
    reporter = ReportAgent()
    scenarios_csv = reporter.build_csv(final_records)
    channel_csv = reporter.build_channel_csv(final_records)
    scenarios_path = run_dir / "scenarios.csv"
    channel_path = run_dir / "channel_gsnr.csv"
    records_path = run_dir / "records.jsonl"
    summary_path = run_dir / "summary.json"
    report_path = run_dir / "report.txt"

    report = reporter.build_report(
        USER_REQUEST,
        run_data["task2"],
        run_data["path_bundle"],
        final_analysis,
        reflection2,
        [
            {
                "iteration": 1,
                "task": None,
                "scenario_count": 2048,
                "reflection": run_data["reflection1"],
                "analysis": _analysis_summary(run_data["analysis1"]),
            },
            {
                "iteration": 2,
                "task": run_data["task2"],
                "scenario_count": 6144,
                "reflection": reflection2,
                "analysis": _analysis_summary(analysis_before_recovery),
            },
        ],
    )
    recovery_note = (
        "\n\nExecution recovery note: this new reproduction was resumed from its saved "
        "scenario and agent logs after interruption. The nine original one-active-slot "
        "GNPy failures were retained in the execution log and individually replayed "
        "once using the corrected single-channel spectral-width handling. Final dataset "
        f"counts are {len(final_records)} scenarios with statuses {_counts({r['scenario']['scenario_id']: {'output': r['output']} for r in final_records})}. "
        "This is not a reconstruction of the unlogged historical LLM run."
    )
    report = report.rstrip() + recovery_note + "\n"

    scenarios_path.write_text(scenarios_csv, encoding="utf-8")
    channel_path.write_text(channel_csv, encoding="utf-8")
    with records_path.open("w", encoding="utf-8") as handle:
        for record in final_records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    report_path.write_text(report, encoding="utf-8")

    manifest = run_data["manifest"]
    summary = {
        "run_id": run_dir.name,
        "evidence_status": manifest.get("evidence_status"),
        "user_request": USER_REQUEST,
        "task": run_data["task2"],
        "path_info": run_data["path_bundle"],
        "iterations": [
            {"iteration": 1, "scenario_count": 2048, "analysis": _analysis_summary(run_data["analysis1"]), "reflection": run_data["reflection1"]},
            {"iteration": 2, "scenario_count": 6144, "analysis_before_recovery": _analysis_summary(analysis_before_recovery), "reflection": reflection2},
        ],
        "initial_execution_counts_from_original_log": initial_counts,
        "completed_execution_counts_before_recovery": pre_recovery_counts,
        "final_dataset_counts": _counts({r["scenario"]["scenario_id"]: {"output": r["output"]} for r in final_records}),
        "recovered_original_failure_ids": recovered_ids,
        "analysis_final": _analysis_summary(final_analysis),
        "agent_logs": {
            "original_execution": str(run_data["execution_log"]),
            "resumed_execution": str(resume_log),
        },
        "raw_gnpy_artifact_note": "Per-scenario GNPy request/result/spectrum files remain in the distinct runtime directories referenced by records.jsonl; they were not copied or overwritten during resumption.",
        "report": report,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return {
        "run_dir": str(run_dir),
        "scenarios_csv": str(scenarios_path),
        "channel_gsnr_csv": str(channel_path),
        "records_jsonl": str(records_path),
        "summary_json": str(summary_path),
        "report_txt": str(report_path),
        "llm_trace_dir": str(run_data["trace_dir"]),
    }


def resume(run_dir: Path, env_path: Path, plan_only: bool = False) -> None:
    run_data = _load_run(run_dir)
    scenarios = run_data["scenarios"]
    scenario_by_id = run_data["scenario_by_id"]
    original = run_data["original_outputs"]
    resume_log = run_dir / "agent_logs" / RESUME_LOG_NAME
    resumed = _load_resume_outputs(resume_log, scenario_by_id)
    latest = dict(original)
    latest.update(resumed)
    missing = [item for item in scenarios if str(item["scenario_id"]) not in latest]
    original_errors = [
        scenario_id for scenario_id, payload in original.items()
        if payload.get("output", {}).get("status") == "error"
    ]
    retried = {
        scenario_id for scenario_id, payload in resumed.items()
        if payload.get("_resume_action") == "retry_original_failure"
    }
    pending_retries = [scenario_id for scenario_id in original_errors if scenario_id not in retried]
    trace_calls = _jsonl_records(run_data["trace_dir"] / "calls.jsonl")
    plan = {
        "run_id": run_dir.name,
        "scenario_count": len(scenarios),
        "scenario_counts_by_iteration": dict(sorted(collections.Counter(str(s.get("iteration")) for s in scenarios).items())),
        "original_completed_unique": len(original),
        "resumed_completed_unique": len(resumed),
        "missing_scenarios_to_execute": len(missing),
        "original_error_scenarios": len(original_errors),
        "original_error_ids": original_errors,
        "original_error_retries_pending": len(pending_retries),
        "successful_llm_calls_already_saved": [item.get("call_id") for item in trace_calls if item.get("status") == "success"],
        "next_expected_reflection_call": "call_006_reflection_agent" if not any(item.get("call_name") == "reflection_agent" and item.get("status") == "success" and int(item.get("call_id", "call_000").split("_")[1]) >= 6 for item in trace_calls) else "already_saved",
        "runtime_artifacts_directory": "a new isolated per-resumption GNPY_CLI_RUNTIME_DIR",
    }
    print(json.dumps(plan, indent=2))
    if plan_only:
        return

    if len(scenarios) != 8192 or len(original) != 4505:
        raise RuntimeError("Preflight counts differ from the authorized interrupted run; refusing execution.")
    final_reflection_saved = any(
        item.get("call_name") == "reflection_agent"
        and item.get("status") == "success"
        and int(str(item.get("call_id", "call_000")).split("_")[1]) >= 6
        for item in trace_calls
    )
    if (
        len(missing) == 0
        and not pending_retries
        and final_reflection_saved
        and (run_dir / "records.jsonl").exists()
    ):
        _refresh_completed_trace_manifest(run_data, resume_log)
        print("Run already has complete execution, final reflection, and output artifacts.")
        return

    runtime_dir = run_dir.parent / f".gnpy_cli_runtime_resume_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.environ["GNPY_CLI_RUNTIME_DIR"] = str(runtime_dir)
    # The source files and their hashes are already captured in the original
    # run manifest. Use those exact local files rather than relying on the
    # original run's PostgreSQL service being available.
    os.environ["PHYSICAL_LAYER_DB_BACKEND"] = "file"
    os.environ.pop("TOPOLOGY_PATH", None)
    os.environ.pop("EQUIPMENT_PATH", None)
    topology_path = run_data["manifest"]["input_files"]["topology"]["path"]
    equipment_path = run_data["manifest"]["input_files"]["equipment"]["path"]

    log_handle = resume_log.open("a", encoding="utf-8", buffering=1)
    current_action = {"value": "missing_scenario"}
    pending_lines = 0

    def append_execution(payload: dict[str, Any]) -> None:
        nonlocal pending_lines
        payload["_resume_action"] = current_action["value"]
        log_handle.write(
            f"[{datetime.now(timezone.utc).isoformat()}] "
            + json.dumps(payload, ensure_ascii=False, default=str)
            + "\n"
        )
        log_handle.flush()
        pending_lines += 1
        if pending_lines >= 25:
            os.fsync(log_handle.fileno())
            pending_lines = 0

    def capture_executor_log(raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict) and payload.get("scenario_id") and "output" in payload:
            append_execution(payload)

    executor = ExecutionAgent(
        log_hook=capture_executor_log,
        topology_path=topology_path,
        equipment_path=equipment_path,
    )
    executor._load_topology_and_equipment()
    for kind, source_path in (("topology", Path(topology_path)), ("equipment", Path(equipment_path))):
        expected = run_data["manifest"]["input_files"][kind].get("sha256")
        actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"The {kind} source file no longer matches the captured run manifest.")
    records_by_id = {
        scenario_id: _as_record(payload, scenario_by_id[scenario_id])
        for scenario_id, payload in latest.items()
    }
    try:
        for index, scenario in enumerate(missing, start=1):
            scenario_id = str(scenario["scenario_id"])
            current_action["value"] = "missing_scenario"
            try:
                record = executor.execute(scenario)
            except Exception as exc:  # keep failures explicit and resumable
                error_payload = {
                    "scenario_id": scenario_id,
                    "engine": scenario.get("preferred_engine", "gnpy"),
                    "sim_config": {},
                    "output": {
                        "engine": scenario.get("preferred_engine", "gnpy"),
                        "backend": "real",
                        "status": "not_executed",
                        "metrics": {"scenario_id": scenario_id, "error_message": f"Resume aborted before a result was produced: {type(exc).__name__}: {exc}"},
                        "artifacts": {},
                    },
                }
                current_action["value"] = "uncaught_not_completed"
                append_execution(error_payload)
                raise
            records_by_id[scenario_id] = record
            if index % 100 == 0 or index == len(missing):
                print(f"RESUME_PROGRESS missing={index}/{len(missing)} scenario={scenario_id}", flush=True)

        # Preserve the pre-recovery state for the workflow's final reflection.
        # The final iteration reflection belongs to the pre-recovery dataset,
        # in which the nine original old-code errors were still errors. Their
        # fixed-code replay results are used only in the final released table.
        pre_recovery_records_by_id = dict(records_by_id)
        for scenario_id in original_errors:
            pre_recovery_records_by_id[scenario_id] = _as_record(
                original[scenario_id], scenario_by_id[scenario_id]
            )
        ordered_before = [pre_recovery_records_by_id[str(item["scenario_id"])] for item in scenarios]
        analysis_agent = ResultsAnalysisAgent()
        analysis_before = analysis_agent.analyze(ordered_before)

        calls_path = run_data["trace_dir"] / "calls.jsonl"
        calls = _jsonl_records(calls_path)
        reflection2 = _reflection_from_saved_success(
            run_data["trace_dir"], calls, analysis_before, run_data["task2"]
        )
        if reflection2 is None:
            _apply_manifest_llm_settings(run_data["manifest"], env_path)
            import utils.llm_audit as audit

            audit._TRACE_DIR = run_data["trace_dir"]
            audit._MANIFEST_PATH = run_data["manifest_path"]
            call_numbers = [
                int(str(item.get("call_id", "call_000")).split("_")[1])
                for item in calls
                if str(item.get("call_id", "")).startswith("call_")
            ]
            audit._CALL_SEQUENCE = max(call_numbers, default=0)
            reflection2 = ReflectionAgent().reflect(analysis_before, run_data["task2"])

        # Retry only the nine errors produced by the old single-active-channel
        # GNPy code. A retry result is appended, leaving original logs untouched.
        current_action["value"] = "retry_original_failure"
        resumed = _load_resume_outputs(resume_log, scenario_by_id)
        completed_retries = {
            scenario_id for scenario_id, payload in resumed.items()
            if payload.get("_resume_action") == "retry_original_failure"
        }
        for scenario_id in original_errors:
            if scenario_id in completed_retries:
                continue
            scenario = scenario_by_id[scenario_id]
            record = executor.execute(scenario)
            records_by_id[scenario_id] = record
            print(f"RECOVERED_ORIGINAL_FAILURE {scenario_id} status={record['output'].get('status')}", flush=True)

        if len(records_by_id) != len(scenarios):
            raise RuntimeError(f"Final record map has {len(records_by_id)} entries for {len(scenarios)} scenarios.")
        final_records = [records_by_id[str(item["scenario_id"])] for item in scenarios]
        final_analysis = analysis_agent.analyze(final_records)
        initial_counts = _counts(original)
        pre_recovery_counts = _counts({
            scenario_id: {"output": record.get("output", {})}
            for scenario_id, record in pre_recovery_records_by_id.items()
        })
        artifacts = _write_final_artifacts(
            run_dir,
            run_data,
            final_records,
            analysis_before,
            final_analysis,
            reflection2,
            original_errors,
            initial_counts,
            pre_recovery_counts,
            resume_log,
        )

        import utils.llm_audit as audit

        audit._TRACE_DIR = run_data["trace_dir"]
        audit._MANIFEST_PATH = run_data["manifest_path"]
        calls = _jsonl_records(calls_path)
        call_numbers = [
            int(str(item.get("call_id", "call_000")).split("_")[1])
            for item in calls
            if str(item.get("call_id", "")).startswith("call_")
        ]
        audit._CALL_SEQUENCE = max(call_numbers, default=0)
        resumption_counts = _refresh_completed_trace_manifest(run_data, resume_log)
        audit.finish_llm_trace(
            "completed_resumed",
            extra={
                "run_dir": str(run_dir),
                "record_count": len(final_records),
                "iterations": 2,
                "resumed_missing_scenarios": resumption_counts["missing_scenarios_executed"],
                "retried_original_failures": resumption_counts["original_failures_replayed"],
                "final_status_counts": _counts({r["scenario"]["scenario_id"]: {"output": r["output"]} for r in final_records}),
            },
        )
        manifest = json.loads(run_data["manifest_path"].read_text(encoding="utf-8"))
        manifest["resumption"]["final_artifacts"] = artifacts
        run_data["manifest_path"].write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("RESUME_COMPLETE " + json.dumps({"artifacts": artifacts, "counts": _counts({r["scenario"]["scenario_id"]: {"output": r["output"]} for r in final_records})}, ensure_ascii=False), flush=True)
    finally:
        log_handle.flush()
        os.fsync(log_handle.fileno())
        log_handle.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--env-file", default="/home/ubuntu/Desktop/Decomposition-code/.env")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    run_dir = PROJECT_DIR / "runs" / args.run_id
    resume(run_dir, Path(args.env_file), args.plan_only)


if __name__ == "__main__":
    main()
