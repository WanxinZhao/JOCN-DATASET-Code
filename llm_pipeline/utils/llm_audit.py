"""Structured, secret-safe audit records for LLM-assisted reproductions."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit


_LOCK = threading.Lock()
_TRACE_DIR: Path | None = None
_MANIFEST_PATH: Path | None = None
_CALL_SEQUENCE = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> str:
    return str(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> Dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        return {"path": str(resolved), "exists": False}
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "exists": True,
        "bytes": stat.st_size,
        "sha256": _sha256(resolved),
        "last_write_time_utc": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def _package_versions(names: Iterable[str]) -> Dict[str, str | None]:
    versions: Dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _git_record(repo_root: Path) -> Dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            return result.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {
        "repository_root": str(repo_root.resolve()),
        "commit": commit,
        "dirty": bool(status) if status is not None else None,
        "status_porcelain": status.splitlines() if status else [],
    }


def _safe_base_url(value: str | None) -> Dict[str, Any]:
    if not value:
        return {"mode": "openai_default", "url": None}
    parts = urlsplit(value)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    sanitized = urlunsplit((parts.scheme, host, parts.path, "", ""))
    return {"mode": "custom", "url": sanitized}


def _optional_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _pricing_record() -> Dict[str, Any]:
    return {
        "currency": "USD",
        "unit": "per_1m_tokens",
        "snapshot_date": os.getenv("OPENAI_PRICING_SNAPSHOT_DATE") or None,
        "input": _optional_float("OPENAI_INPUT_USD_PER_1M_TOKENS"),
        "cached_input": _optional_float("OPENAI_CACHED_INPUT_USD_PER_1M_TOKENS"),
        "output": _optional_float("OPENAI_OUTPUT_USD_PER_1M_TOKENS"),
        "source": os.getenv("OPENAI_PRICING_SOURCE") or None,
        "note": "Cost is calculated only when an explicit dated pricing snapshot is configured.",
    }


def start_llm_trace(
    run_id: str,
    *,
    base_dir: str | Path = "data/runs",
    user_request: str,
    input_paths: Mapping[str, str | Path] | None = None,
    human_interventions: list[Mapping[str, Any]] | None = None,
) -> Path:
    """Start a trace and return its directory.

    API keys and proxy credentials are deliberately never persisted.
    """
    global _TRACE_DIR, _MANIFEST_PATH, _CALL_SEQUENCE

    repo_root = Path(__file__).resolve().parents[1]
    trace_dir = Path(base_dir) / run_id / "llm_trace"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / "requests").mkdir(exist_ok=True)
    (trace_dir / "responses").mkdir(exist_ok=True)

    with _LOCK:
        _TRACE_DIR = trace_dir
        _MANIFEST_PATH = trace_dir / "run_manifest.json"
        _CALL_SEQUENCE = 0

        source_paths = {
            "utils/llm.py": repo_root / "utils" / "llm.py",
            "utils/llm_audit.py": repo_root / "utils" / "llm_audit.py",
            "planner_agent.py": repo_root / "orchestrator" / "planner_agent.py",
            "scenario_expander_agent.py": repo_root
            / "orchestrator"
            / "scenario_expander_agent.py",
            "reflection_agent.py": repo_root / "orchestrator" / "reflection_agent.py",
            "orchestrator.py": repo_root / "orchestrator" / "orchestrator.py",
        }
        manifest: Dict[str, Any] = {
            "trace_schema_version": "1.0",
            "run_id": run_id,
            "trace_status": "running",
            "evidence_status": "new_reproduction_not_original_historical_trace",
            "historical_provider_author_confirmation": "OpenAI",
            "started_at_utc": _utc_now(),
            "finished_at_utc": None,
            "user_request": user_request,
            "human_interventions": human_interventions or [],
            "runtime": {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "python_executable": sys.executable,
                "packages": _package_versions(
                    ["openai", "httpx", "gnpy", "numpy", "scipy", "pandas"]
                ),
            },
            "git": _git_record(repo_root),
            "llm_configuration": {
                "provider": os.getenv("LLM_PROVIDER", "openai").strip().lower(),
                "fallback_provider": os.getenv("LLM_FALLBACK_PROVIDER") or None,
                "openai_model_requested": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "openai_base_url": _safe_base_url(os.getenv("OPENAI_BASE_URL")),
                "openai_api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
                "temperature": 0.2,
                "system_message": "You are an optical network expert.",
                "timeout_seconds": os.getenv("LLM_TIMEOUT_SECONDS", "90"),
                "maximum_attempts_per_provider": os.getenv("LLM_MAX_RETRIES", "3"),
                "strict_mode": os.getenv("LLM_STRICT_MODE", "0"),
                "pricing": _pricing_record(),
            },
            "source_files": {
                name: _file_record(path) for name, path in source_paths.items()
            },
            "input_files": {
                name: _file_record(Path(path))
                for name, path in (input_paths or {}).items()
            },
            "summary": {
                "high_level_calls": 0,
                "api_attempts": 0,
                "successful_attempts": 0,
                "failed_attempts": 0,
                "total_tokens": 0,
                "estimated_cost_usd": None,
            },
        }
        _write_json(_MANIFEST_PATH, manifest)
    return trace_dir


def next_call_id(call_name: str) -> str:
    global _CALL_SEQUENCE
    with _LOCK:
        _CALL_SEQUENCE += 1
        safe_name = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_" for char in call_name
        ).strip("_") or "llm"
        return f"call_{_CALL_SEQUENCE:03d}_{safe_name}"


def _redact_text(value: str) -> str:
    redacted = value
    for variable_name in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
        secret = os.getenv(variable_name)
        if secret:
            redacted = redacted.replace(secret, f"<{variable_name}_REDACTED>")
    return redacted


def _usage_record(response_payload: Mapping[str, Any] | None) -> Dict[str, int]:
    usage = dict((response_payload or {}).get("usage") or {})
    details = dict(usage.get("prompt_tokens_details") or {})
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "cached_prompt_tokens": int(details.get("cached_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def _estimated_cost(usage: Mapping[str, int]) -> float | None:
    pricing = _pricing_record()
    input_rate = pricing["input"]
    output_rate = pricing["output"]
    if input_rate is None or output_rate is None or not pricing["snapshot_date"]:
        return None
    cached_rate = pricing["cached_input"]
    cached_tokens = usage["cached_prompt_tokens"] if cached_rate is not None else 0
    regular_input = max(0, usage["prompt_tokens"] - cached_tokens)
    cost = regular_input * input_rate / 1_000_000
    cost += usage["completion_tokens"] * output_rate / 1_000_000
    if cached_rate is not None:
        cost += cached_tokens * cached_rate / 1_000_000
    return round(cost, 10)


def record_request_start(
    *,
    call_id: str,
    call_name: str,
    provider: str,
    attempt: int,
    maximum_attempts: int,
    request_payload: Mapping[str, Any],
    started_at_utc: str,
) -> None:
    """Persist the exact payload before the network request begins."""
    if _TRACE_DIR is None:
        return
    provider_slug = provider.lower().replace(" ", "_")
    stem = f"{call_id}_{provider_slug}_attempt_{attempt:02d}"
    request_path = _TRACE_DIR / "requests" / f"{stem}.json"
    request_record = {
        "call_id": call_id,
        "call_name": call_name,
        "provider": provider,
        "attempt": attempt,
        "maximum_attempts": maximum_attempts,
        "sent_at_utc": started_at_utc,
        "payload": request_payload,
    }
    with _LOCK:
        _write_json(request_path, request_record)


def record_attempt(
    *,
    call_id: str,
    call_name: str,
    provider: str,
    attempt: int,
    maximum_attempts: int,
    request_payload: Mapping[str, Any],
    started_at_utc: str,
    finished_at_utc: str,
    latency_seconds: float,
    response_payload: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
    retryable: bool | None = None,
) -> None:
    if _TRACE_DIR is None:
        return

    provider_slug = provider.lower().replace(" ", "_")
    stem = f"{call_id}_{provider_slug}_attempt_{attempt:02d}"
    request_path = _TRACE_DIR / "requests" / f"{stem}.json"
    response_path = _TRACE_DIR / "responses" / f"{stem}.json"
    usage = _usage_record(response_payload)
    cost = _estimated_cost(usage)

    request_record = {
        "call_id": call_id,
        "call_name": call_name,
        "provider": provider,
        "attempt": attempt,
        "maximum_attempts": maximum_attempts,
        "sent_at_utc": started_at_utc,
        "payload": request_payload,
    }
    response_record: Dict[str, Any] | None = None
    if response_payload is not None:
        response_record = {
            "call_id": call_id,
            "call_name": call_name,
            "provider": provider,
            "attempt": attempt,
            "received_at_utc": finished_at_utc,
            "payload": response_payload,
        }

    summary = {
        "call_id": call_id,
        "call_name": call_name,
        "provider": provider,
        "attempt": attempt,
        "maximum_attempts": maximum_attempts,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "latency_seconds": round(latency_seconds, 6),
        "status": "error" if error else "success",
        "retryable": retryable,
        "request_file": str(request_path.relative_to(_TRACE_DIR)),
        "response_file": (
            str(response_path.relative_to(_TRACE_DIR)) if response_record else None
        ),
        "response_id": (response_payload or {}).get("id"),
        "response_model": (response_payload or {}).get("model"),
        "system_fingerprint": (response_payload or {}).get("system_fingerprint"),
        "finish_reasons": [
            choice.get("finish_reason")
            for choice in (response_payload or {}).get("choices", [])
            if isinstance(choice, dict)
        ],
        "usage": usage,
        "estimated_cost_usd": cost,
        "error_type": type(error).__name__ if error else None,
        "error_message": _redact_text(str(error)) if error else None,
    }

    with _LOCK:
        _write_json(request_path, request_record)
        if response_record:
            _write_json(response_path, response_record)
        _append_jsonl(_TRACE_DIR / "calls.jsonl", summary)
        if error:
            _append_jsonl(_TRACE_DIR / "errors.jsonl", summary)


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _write_call_summary(trace_dir: Path, records: list[Dict[str, Any]]) -> None:
    fields = [
        "call_id",
        "call_name",
        "provider",
        "attempt",
        "status",
        "latency_seconds",
        "response_id",
        "response_model",
        "system_fingerprint",
        "prompt_tokens",
        "cached_prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "error_type",
        "error_message",
    ]
    with (trace_dir / "call_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            usage = record.get("usage", {})
            writer.writerow(
                {
                    **{name: record.get(name) for name in fields},
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "cached_prompt_tokens": usage.get("cached_prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
            )


def finish_llm_trace(
    status: str,
    *,
    error: BaseException | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    if _TRACE_DIR is None or _MANIFEST_PATH is None or not _MANIFEST_PATH.exists():
        return
    with _LOCK:
        records = _read_jsonl(_TRACE_DIR / "calls.jsonl")
        costs = [
            float(record["estimated_cost_usd"])
            for record in records
            if record.get("estimated_cost_usd") is not None
        ]
        unique_calls = {record.get("call_id") for record in records}
        manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest["trace_status"] = status
        manifest["finished_at_utc"] = _utc_now()
        manifest["error"] = (
            {
                "type": type(error).__name__,
                "message": _redact_text(str(error)),
            }
            if error
            else None
        )
        manifest["summary"] = {
            "high_level_calls": len(unique_calls),
            "api_attempts": len(records),
            "successful_attempts": sum(record.get("status") == "success" for record in records),
            "failed_attempts": sum(record.get("status") == "error" for record in records),
            "total_tokens": sum(int(record.get("usage", {}).get("total_tokens", 0)) for record in records),
            "estimated_cost_usd": round(sum(costs), 10) if len(costs) == len(records) and records else None,
        }
        if extra:
            manifest["run_result"] = dict(extra)
        _write_json(_MANIFEST_PATH, manifest)
        _write_call_summary(_TRACE_DIR, records)


def current_trace_dir() -> str | None:
    return str(_TRACE_DIR) if _TRACE_DIR else None
