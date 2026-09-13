from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import re
import traceback
from typing import Any

import pandas as pd
import streamlit as st

from orchestrator.orchestrator import Orchestrator
from orchestrator.physical_layer_store import PhysicalLayerStore


ROOT_DIR = Path(__file__).resolve().parent
RUNS_DIR = ROOT_DIR / "data" / "runs"
DEFAULT_REQUEST = (
    "Generate a raw physical-layer transmission dataset for all NDFF paths starting from Bristol. "
    "Use 3 Voyager transmitters only. Simulate only QPSK and 16QAM coherent transmission."
)
LOG_LINE_PATTERN = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s*(?P<payload>.*)$")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_artifact_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value.replace("\\", "/"))
    if path.is_absolute():
        return path
    return (ROOT_DIR / path).resolve()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _format_timestamp(value: str | None) -> str:
    if not value:
        return ""
    return value.split(" ")[-1]


def _title_case_agent(name: str) -> str:
    return name.replace("_", " ").replace("agent", "Agent").title()


def _summarize_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "Network": task.get("network"),
        "Source": task.get("source"),
        "Destination": task.get("destination") or "All reachable destinations",
        "Scope": task.get("scope"),
        "Task": task.get("task"),
        "Mode": task.get("task_mode"),
        "Preferred Engine": task.get("preferred_engine"),
        "Modulations": ", ".join(task.get("modulations", [])) or None,
        "Transmitters": json.dumps(task.get("transmitter_inventory", {}), ensure_ascii=True),
        "Channel Slots": task.get("channel_slots"),
        "Pattern": task.get("fixed_channel_pattern") or task.get("channel_activation_mode"),
    }


def _parse_agent_log(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not path.exists():
        return entries
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = LOG_LINE_PATTERN.match(line)
        if not match:
            entries.append({"timestamp": None, "payload": line, "data": None})
            continue
        payload = match.group("payload")
        data: Any = None
        if payload.startswith("{") or payload.startswith("["):
            with contextlib.suppress(json.JSONDecodeError):
                data = json.loads(payload)
        entries.append({"timestamp": match.group("timestamp"), "payload": payload, "data": data})
    return entries


def load_agent_logs(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    parsed: dict[str, list[dict[str, Any]]] = {}
    for agent_name, relative_path in summary.get("agent_logs", {}).items():
        path = _resolve_artifact_path(relative_path)
        if path:
            parsed[agent_name] = _parse_agent_log(path)
    return parsed


@st.cache_data(show_spinner=False)
def list_runs() -> list[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        [item.name for item in RUNS_DIR.iterdir() if item.is_dir()],
        reverse=True,
    )


@st.cache_data(show_spinner=False)
def load_json(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def load_jsonl(path_str: str) -> list[dict[str, Any]]:
    path = Path(path_str)
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_run_paths(run_id: str) -> dict[str, Path]:
    run_dir = RUNS_DIR / run_id
    return {
        "run_dir": run_dir,
        "summary": run_dir / "summary.json",
        "records": run_dir / "records.jsonl",
        "report": run_dir / "report.txt",
        "scenarios_csv": run_dir / "scenarios.csv",
        "channel_gsnr_csv": run_dir / "channel_gsnr.csv",
    }


def execute_request(user_request: str) -> tuple[dict[str, Any] | None, str, str | None]:
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            orchestrator = Orchestrator()
            result = orchestrator.run(user_request)
        return result, buffer.getvalue(), None
    except Exception as exc:  # pragma: no cover - defensive UI path
        traceback.print_exc(file=buffer)
        return None, buffer.getvalue(), str(exc)


def summarize_run(summary: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [item.get("output", {}).get("status", "unknown") for item in records]
    ok_count = sum(1 for status in statuses if status == "ok")
    invalid_count = sum(1 for status in statuses if status == "invalid_metrics")
    error_count = sum(1 for status in statuses if status not in {"ok", "invalid_metrics"})
    return {
        "task_mode": summary.get("task", {}).get("task_mode"),
        "network": summary.get("task", {}).get("network"),
        "source": summary.get("task", {}).get("source"),
        "scenario_count": len(records),
        "ok_count": ok_count,
        "invalid_count": invalid_count,
        "error_count": error_count,
    }


def build_scenario_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in records:
        scenario = item.get("scenario", {})
        output = item.get("output", {})
        metrics = output.get("metrics", {})
        rows.append(
            {
                "scenario_id": scenario.get("scenario_id"),
                "iteration": scenario.get("iteration"),
                "source": scenario.get("source"),
                "destination": scenario.get("destination"),
                "distance_km": _safe_float(scenario.get("distance")),
                "modulation": scenario.get("modulation"),
                "tx": scenario.get("transmitter_type"),
                "power_dbm": _safe_float(scenario.get("power")),
                "pattern": scenario.get("channel_pattern"),
                "engine": output.get("engine") or item.get("tool"),
                "status": output.get("status"),
                "snr_db": _safe_float(metrics.get("effective_snr_db")),
                "osnr_db": _safe_float(metrics.get("osnr_signal_bw_db")),
                "ber": _safe_float(metrics.get("ber")),
                "gmi": _safe_float(metrics.get("gmi")),
                "spans": metrics.get("spans_simulated"),
                "artifacts": output.get("artifacts", {}),
            }
        )
    return rows


def show_overview(summary: dict[str, Any], records: list[dict[str, Any]], paths: dict[str, Path]) -> None:
    run_summary = summarize_run(summary, records)
    task = summary.get("task", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Scenarios", run_summary["scenario_count"])
    col2.metric("OK", run_summary["ok_count"])
    col3.metric("Invalid", run_summary["invalid_count"])
    col4.metric("Errors", run_summary["error_count"])

    st.subheader("Task")
    st.json(
        {
            "network": task.get("network"),
            "source": task.get("source"),
            "scope": task.get("scope"),
            "task_mode": task.get("task_mode"),
            "preferred_engine": task.get("preferred_engine"),
            "modulations": task.get("modulations"),
            "transmitter_inventory": task.get("transmitter_inventory"),
            "channel_slots": task.get("channel_slots"),
            "notes": task.get("notes"),
        },
        expanded=False,
    )

    paths_info = summary.get("path_info", {}).get("paths", [])
    if paths_info:
        st.subheader("Resolved Paths")
        path_rows = [
            {
                "destination": path.get("destination"),
                "nodes": " -> ".join(path.get("nodes", [])),
                "distance_km": path.get("total_distance_km"),
                "spans": len(path.get("spans", [])),
            }
            for path in paths_info
        ]
        st.dataframe(pd.DataFrame(path_rows), use_container_width=True, hide_index=True)

    if paths["report"].exists():
        st.subheader("Report")
        st.code(paths["report"].read_text(encoding="utf-8"), language="text")


def show_scenarios(rows: list[dict[str, Any]], key_prefix: str) -> list[dict[str, Any]]:
    all_destinations = sorted({row["destination"] for row in rows if row["destination"]})
    all_modulations = sorted({row["modulation"] for row in rows if row["modulation"]})
    all_statuses = sorted({row["status"] for row in rows if row["status"]})

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    selected_destinations = filter_col1.multiselect(
        "Destination",
        all_destinations,
        default=all_destinations,
        key=f"{key_prefix}_destination",
    )
    selected_modulations = filter_col2.multiselect(
        "Modulation",
        all_modulations,
        default=all_modulations,
        key=f"{key_prefix}_modulation",
    )
    selected_statuses = filter_col3.multiselect(
        "Status",
        all_statuses,
        default=all_statuses,
        key=f"{key_prefix}_status",
    )

    filtered = [
        row
        for row in rows
        if (not selected_destinations or row["destination"] in selected_destinations)
        and (not selected_modulations or row["modulation"] in selected_modulations)
        and (not selected_statuses or row["status"] in selected_statuses)
    ]

    table = pd.DataFrame(
        [
            {
                key: value
                for key, value in row.items()
                if key != "artifacts"
            }
            for row in filtered
        ]
    )
    st.dataframe(table, use_container_width=True, hide_index=True)
    return filtered


def show_images(artifacts: dict[str, Any]) -> None:
    image_keys = [
        ("tx_psd_png", "TX PSD"),
        ("rx_psd_png", "RX PSD"),
        ("tx_center_constellation_png", "TX Center Reference"),
        ("rx_frontend_constellation_png", "RX Frontend"),
        ("rx_after_edc_constellation_png", "RX After EDC"),
        ("rx_after_equalizer_constellation_png", "RX After Equalizer"),
        ("rx_after_cpr_constellation_png", "RX After CPR"),
        ("eye_diagram_png", "RX Eye / DSP Progression"),
    ]
    existing = [(label, _resolve_artifact_path(artifacts.get(key))) for key, label in image_keys]
    existing = [(label, path) for label, path in existing if path and path.exists()]
    if not existing:
        st.info("No image artifacts found for this scenario.")
        return

    for start in range(0, len(existing), 2):
        columns = st.columns(2)
        pair = existing[start : start + 2]
        for index, (label, path) in enumerate(pair):
            with columns[index]:
                st.caption(label)
                st.image(str(path), use_column_width=True)


def show_step_header(index: int, title: str, timestamp: str | None = None) -> None:
    suffix = f"  {timestamp}" if timestamp else ""
    st.markdown(f"**{index}. {title}{suffix}**")


def show_execution_flow(summary: dict[str, Any], records: list[dict[str, Any]], run_id: str) -> None:
    agent_logs = load_agent_logs(summary)
    orchestrator_logs = agent_logs.get("orchestrator", [])
    planner_logs = agent_logs.get("planner_agent", [])
    scenario_logs = agent_logs.get("scenario_expander_agent", [])
    execution_logs = agent_logs.get("execution_agent", [])
    analysis_logs = agent_logs.get("results_analysis_agent", [])
    reflection_logs = agent_logs.get("reflection_agent", [])
    report_logs = agent_logs.get("report_agent", [])

    st.caption(f"Run `{run_id}` execution flow")

    source_description = PhysicalLayerStore().describe()
    top_step = st.container(border=True)
    with top_step:
        show_step_header(1, "Connect To Physical-Layer Data Source")
        st.write(source_description)
        first_orchestrator = orchestrator_logs[0] if orchestrator_logs else None
        if first_orchestrator and isinstance(first_orchestrator.get("payload"), str):
            st.caption(first_orchestrator["payload"])

    path_step = st.container(border=True)
    with path_step:
        topology_log = next(
            (entry for entry in orchestrator_logs if isinstance(entry.get("data"), dict) and entry["data"].get("paths")),
            None,
        )
        path_info = summary.get("path_info", {}).get("paths", [])
        path_count = len(path_info)
        step_title = f"Load Topology And Resolve Paths ({path_count} path{'s' if path_count != 1 else ''})"
        show_step_header(2, step_title, _format_timestamp(topology_log.get("timestamp") if topology_log else None))
        if topology_log and isinstance(topology_log.get("data"), dict):
            data = topology_log["data"]
            st.write(
                f"Network `{data.get('network')}` from source `{data.get('source')}` resolved "
                f"`{len(data.get('paths', []))}` destination paths."
            )
        if path_info:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Destination": path.get("destination"),
                            "Nodes": " -> ".join(path.get("nodes", [])),
                            "Distance (km)": path.get("total_distance_km"),
                            "Spans": len(path.get("spans", [])),
                        }
                        for path in path_info
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

    iterations = summary.get("iterations", [])
    for iteration_index, iteration in enumerate(iterations, start=1):
        iteration_id = iteration.get("iteration", iteration_index)
        planner_log = next(
            (
                entry
                for entry in planner_logs
                if isinstance(entry.get("data"), dict) and entry["data"].get("task_mode") == iteration.get("task", {}).get("task_mode")
            ),
            planner_logs[0] if planner_logs else None,
        )
        scenario_log = next(
            (
                entry
                for entry in scenario_logs
                if isinstance(entry.get("data"), dict) and entry["data"].get("iteration") == iteration_id
            ),
            None,
        )
        analysis_log = next(
            (
                entry
                for entry in analysis_logs
                if isinstance(entry.get("data"), dict) and entry["data"].get("iteration") == iteration_id
            ),
            None,
        )
        reflection_log = next(
            (
                entry
                for entry in reflection_logs
                if isinstance(entry.get("data"), dict) and entry["data"].get("iteration") == iteration_id
            ),
            None,
        )

        st.markdown(f"### Iteration {iteration_id}")

        planner_step = st.container(border=True)
        with planner_step:
            show_step_header(3, "Planner Agent", _format_timestamp(planner_log.get("timestamp") if planner_log else None))
            st.write("Convert the natural-language request into a structured optical-DT task.")
            st.dataframe(
                pd.DataFrame([_summarize_task(iteration.get("task", {}))]),
                use_container_width=True,
                hide_index=True,
            )

        scenario_step = st.container(border=True)
        with scenario_step:
            show_step_header(
                4,
                "Scenario Expander Agent",
                _format_timestamp(scenario_log.get("timestamp") if scenario_log else None),
            )
            scenarios = scenario_log.get("data", {}).get("scenarios", []) if scenario_log else []
            st.write(
                f"Expanded the plan into `{len(scenarios) or iteration.get('scenario_count', 0)}` executable scenarios."
            )
            if scenarios:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Scenario": scenario.get("scenario_id"),
                                "Destination": scenario.get("destination"),
                                "Modulation": scenario.get("modulation"),
                                "Power (dBm)": scenario.get("power"),
                                "Engine": scenario.get("preferred_engine"),
                                "Pattern": scenario.get("channel_pattern"),
                            }
                            for scenario in scenarios
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        execution_step = st.container(border=True)
        with execution_step:
            iteration_records = [
                record for record in records if record.get("scenario", {}).get("iteration") == iteration_id
            ]
            last_execution_log = None
            for entry in execution_logs:
                data = entry.get("data")
                if isinstance(data, dict) and data.get("scenario_id", "").startswith(f"iter-{iteration_id}-"):
                    last_execution_log = entry
            show_step_header(
                5,
                "Execution Agent",
                _format_timestamp(last_execution_log.get("timestamp") if last_execution_log else None),
            )
            status_counts = pd.Series(
                [record.get("output", {}).get("status", "unknown") for record in iteration_records]
            ).value_counts()
            metric_cols = st.columns(4)
            metric_cols[0].metric("Scenarios", len(iteration_records))
            metric_cols[1].metric("OK", int(status_counts.get("ok", 0)))
            metric_cols[2].metric("Invalid", int(status_counts.get("invalid_metrics", 0)))
            metric_cols[3].metric("Errors", int(len(iteration_records) - status_counts.get("ok", 0) - status_counts.get("invalid_metrics", 0)))
            if iteration_records:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Scenario": record.get("scenario", {}).get("scenario_id"),
                                "Destination": record.get("scenario", {}).get("destination"),
                                "Engine": record.get("output", {}).get("engine") or record.get("tool"),
                                "Status": record.get("output", {}).get("status"),
                                "SNR (dB)": record.get("output", {}).get("metrics", {}).get("effective_snr_db"),
                                "OSNR (dB)": record.get("output", {}).get("metrics", {}).get("osnr_signal_bw_db"),
                                "BER": record.get("output", {}).get("metrics", {}).get("ber"),
                            }
                            for record in iteration_records
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        analysis_step = st.container(border=True)
        with analysis_step:
            analysis_data = analysis_log.get("data", {}).get("analysis", {}) if analysis_log else iteration.get("analysis", {})
            show_step_header(
                6,
                "Results Analysis Agent",
                _format_timestamp(analysis_log.get("timestamp") if analysis_log else None),
            )
            metric_cols = st.columns(4)
            metric_cols[0].metric("Best Score", analysis_data.get("best_score"))
            metric_cols[1].metric("OK Scenarios", analysis_data.get("ok_scenarios"))
            metric_cols[2].metric("No Signal", analysis_data.get("no_signal_scenarios"))
            metric_cols[3].metric("Errors", analysis_data.get("error_scenarios"))
            best_result = analysis_data.get("best_result", {})
            best_scenario = best_result.get("scenario", {})
            best_output = best_result.get("output", {})
            if best_scenario:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Best Scenario": best_scenario.get("scenario_id"),
                                "Destination": best_scenario.get("destination"),
                                "Modulation": best_scenario.get("modulation"),
                                "Engine": best_output.get("engine") or best_result.get("tool"),
                                "SNR (dB)": best_output.get("metrics", {}).get("effective_snr_db"),
                                "OSNR (dB)": best_output.get("metrics", {}).get("osnr_signal_bw_db"),
                                "BER": best_output.get("metrics", {}).get("ber"),
                            }
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        reflection_step = st.container(border=True)
        with reflection_step:
            reflection_data = reflection_log.get("data", {}).get("reflection", {}) if reflection_log else iteration.get("reflection", {})
            show_step_header(
                7,
                "Reflection Agent",
                _format_timestamp(reflection_log.get("timestamp") if reflection_log else None),
            )
            action = reflection_data.get("action", "unknown")
            message = reflection_data.get("message") or "No reflection message recorded."
            if action == "stop":
                st.success(f"Action: {action}")
            elif action == "refine":
                st.warning(f"Action: {action}")
            else:
                st.info(f"Action: {action}")
            st.write(message)
            recommendations = reflection_data.get("recommendations", [])
            if recommendations:
                st.dataframe(
                    pd.DataFrame([{"Recommendation": item} for item in recommendations]),
                    use_container_width=True,
                    hide_index=True,
                )

    report_step = st.container(border=True)
    with report_step:
        report_log = report_logs[0] if report_logs else None
        show_step_header(8, "Report Agent", _format_timestamp(report_log.get("timestamp") if report_log else None))
        st.write("Final text summary and saved outputs for this run.")
        report_text = summary.get("report") or (report_log.get("payload") if report_log else "")
        if report_text:
            st.text(report_text)


def show_scenario_detail(
    filtered_rows: list[dict[str, Any]], records: list[dict[str, Any]], key_prefix: str = "detail"
) -> None:
    if not filtered_rows:
        st.info("No scenarios match the current filters.")
        return

    scenario_options = [row["scenario_id"] for row in filtered_rows]
    selected_scenario_id = st.selectbox("Scenario", scenario_options, key=f"{key_prefix}_scenario")
    selected_record = next(
        item for item in records if item.get("scenario", {}).get("scenario_id") == selected_scenario_id
    )

    scenario = selected_record.get("scenario", {})
    output = selected_record.get("output", {})
    metrics = output.get("metrics", {})
    artifacts = output.get("artifacts", {})

    st.subheader("Scenario Summary")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Status", output.get("status", "unknown"))
    col2.metric("SNR (dB)", metrics.get("effective_snr_db"))
    col3.metric("OSNR (dB)", metrics.get("osnr_signal_bw_db"))
    col4.metric("BER", metrics.get("ber"))

    st.json(
        {
            "scenario_id": scenario.get("scenario_id"),
            "source": scenario.get("source"),
            "destination": scenario.get("destination"),
            "distance_km": scenario.get("distance"),
            "modulation": scenario.get("modulation"),
            "transmitter_type": scenario.get("transmitter_type"),
            "channel_pattern": scenario.get("channel_pattern"),
            "baud_rate_gbaud": scenario.get("baud_rate_gbaud"),
            "bit_rate_gbps": scenario.get("bit_rate_gbps"),
            "power_dbm": scenario.get("power"),
            "engine": output.get("engine"),
        },
        expanded=False,
    )

    per_channel_metrics = metrics.get("per_channel_metrics")
    if per_channel_metrics:
        st.subheader("Per-Channel Metrics")
        st.dataframe(pd.DataFrame(per_channel_metrics), use_container_width=True, hide_index=True)

    st.subheader("Artifacts")
    show_images(artifacts)

    with st.expander("Raw Result JSON", expanded=False):
        st.json(selected_record)

    metrics_path = _resolve_artifact_path(artifacts.get("metrics_json"))
    if metrics_path and metrics_path.exists():
        with st.expander("DSP Metrics JSON", expanded=False):
            st.json(load_json(str(metrics_path)))


def show_run_browser(run_id: str) -> None:
    paths = build_run_paths(run_id)
    if not paths["summary"].exists() or not paths["records"].exists():
        st.error("Selected run is missing summary.json or records.jsonl.")
        return

    summary = load_json(str(paths["summary"]))
    records = load_jsonl(str(paths["records"]))
    scenario_rows = build_scenario_rows(records)

    st.caption(f"Run directory: `{paths['run_dir']}`")

    tabs = st.tabs(["Execution Flow", "Overview", "Scenarios", "Scenario Detail"])
    with tabs[0]:
        show_execution_flow(summary, records, run_id)
    with tabs[1]:
        show_overview(summary, records, paths)
    with tabs[2]:
        filtered_rows = show_scenarios(scenario_rows, key_prefix=f"{run_id}_scenarios")
    with tabs[3]:
        filtered_rows = show_scenarios(scenario_rows, key_prefix=f"{run_id}_detail")
        show_scenario_detail(filtered_rows, records, key_prefix=f"{run_id}_detail")


def show_request_runner() -> None:
    st.subheader("Run New Request")
    st.caption("Enter a natural-language request and run the full orchestration pipeline from the web UI.")

    with st.form("request_form", clear_on_submit=False):
        user_request = st.text_area(
            "Request",
            value=st.session_state.get("request_text", DEFAULT_REQUEST),
            height=140,
            placeholder="Describe the optical DT task you want to run...",
        )
        submitted = st.form_submit_button("Run Orchestration", use_container_width=True)

    if submitted:
        st.session_state["request_text"] = user_request
        if not user_request.strip():
            st.warning("Enter a request before running.")
        else:
            with st.spinner("Running orchestration... this may take a while."):
                result, console_output, error_message = execute_request(user_request.strip())
            st.session_state["last_console_output"] = console_output
            st.session_state["last_error_message"] = error_message
            st.session_state["last_run_result"] = result
            if result is not None:
                st.session_state["latest_run_id"] = result.get("run_id")
                st.cache_data.clear()

    console_output = st.session_state.get("last_console_output")
    error_message = st.session_state.get("last_error_message")
    result = st.session_state.get("last_run_result")

    if error_message:
        st.error(f"Run failed: {error_message}")
        if console_output:
            with st.expander("Debug Console Output", expanded=False):
                st.code(console_output, language="text")
        return

    if result:
        artifacts = result.get("artifacts", {})
        run_id = result.get("run_id")
        st.success(f"Run completed. Saved as `{run_id}`.")
        if artifacts:
            cols = st.columns(5)
            cols[0].metric("Run ID", run_id)
            cols[1].metric("Records", len(result.get("records", [])))
            cols[2].metric("Best Score", result.get("analysis", {}).get("best_score"))
            cols[3].metric("Best Engine", result.get("analysis", {}).get("best_result", {}).get("tool"))
            cols[4].metric("Artifacts", "saved")

            st.subheader("Generated Files")
            st.json(artifacts, expanded=False)

        st.subheader("Latest Run View")
        if run_id:
            show_run_browser(str(run_id))

    elif console_output:
        with st.expander("Debug Console Output", expanded=False):
            st.code(console_output, language="text")


def main() -> None:
    st.set_page_config(page_title="Optical DT Web Console", layout="wide")
    st.title("Optical DT Web Console")
    st.caption("Run natural-language orchestration requests and inspect results in the same Streamlit app.")

    store = PhysicalLayerStore()
    st.sidebar.markdown("### Physical-Layer Source")
    st.sidebar.write(store.describe())

    runs = list_runs()
    latest_run_id = st.session_state.get("latest_run_id")
    if latest_run_id and latest_run_id not in runs:
        runs = [latest_run_id] + runs

    tabs = st.tabs(["Run Request", "Browse Runs"])
    with tabs[0]:
        show_request_runner()
    with tabs[1]:
        if not runs:
            st.info(f"No runs found under {RUNS_DIR}")
            return
        default_index = 0
        if latest_run_id and latest_run_id in runs:
            default_index = runs.index(latest_run_id)
        selected_run = st.selectbox("Run", runs, index=default_index, key="browse_run")
        show_run_browser(selected_run)


if __name__ == "__main__":
    main()
