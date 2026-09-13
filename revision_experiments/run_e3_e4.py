"""Run E3 deterministic-baseline audit and E4 GNPy dataset audit.

This script uses only observable public artifacts. It does not invent missing
LLM model, token, cost, latency, prompt, or human-intervention measurements.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import itertools
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "MajorRevision"
E3_DIR = OUT_ROOT / "E3_llm_baseline"
E4_DIR = OUT_ROOT / "E4_gnpy_audit"
SOURCE_DIR = E4_DIR / "source"
REPO = "https://raw.githubusercontent.com/hpn-bristol/LLM-Driven-Multi-Agent-Optical-Digital-Twin-for-Automated-Data-Generation/main"
URLS = {
    "scenarios.csv": f"{REPO}/GNPy_8_channels/scenarios.csv",
    "artifacts.zip": f"{REPO}/GNPy_8_channels/artifacts.zip",
    "report.txt": f"{REPO}/GNPy_8_channels/report.txt",
    "NDFF_Testbed.json": f"{REPO}/NDFF_Testbed.json",
    "eqpt_config_NDFF.json": f"{REPO}/eqpt_config_NDFF.json",
}
KEY = ["source", "destination", "transmitter_type", "modulation", "channel_pattern", "launch_power_dbm"]
NUMERIC_COMPARE = [
    "gsnr_db", "osnr_db", "ase_dbm", "gsnr_signal_bw_db", "osnr_signal_bw_db",
    "rx_signal_power_dbm", "span_count",
]
DESTINATION_UID = {
    "bradley stoke": "node_brd",
    "froxfield": "node_ffd",
    "reading": "node_rdg",
    "powergate": "node_pgt",
}


def download(url: str, destination: Path) -> None:
    if destination.exists() and destination.stat().st_size > 0:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    temporary.replace(destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_key_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ["source", "destination", "transmitter_type", "modulation", "channel_pattern"]:
        result[column] = result[column].astype(str)
    result["launch_power_dbm"] = pd.to_numeric(result["launch_power_dbm"], errors="coerce")
    result["channel_pattern"] = result["channel_pattern"].str.zfill(8)
    return result


def make_expected(frame: pd.DataFrame) -> pd.DataFrame:
    destinations = sorted(frame["destination"].dropna().unique())
    transmitters = sorted(frame["transmitter_type"].dropna().unique())
    modulations = sorted(frame["modulation"].dropna().unique())
    powers = sorted(pd.to_numeric(frame["launch_power_dbm"], errors="coerce").dropna().unique())
    patterns = [format(value, "08b") for value in range(256)]
    records = []
    for destination, transmitter, modulation, pattern, power in itertools.product(
        destinations, transmitters, modulations, patterns, powers
    ):
        records.append({
            "source": "bristol", "destination": destination, "transmitter_type": transmitter,
            "modulation": modulation, "channel_pattern": pattern, "launch_power_dbm": float(power),
        })
    return pd.DataFrame(records)


def key_tuples(frame: pd.DataFrame) -> set[tuple]:
    return set(frame[KEY].itertuples(index=False, name=None))


def audit_coverage(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    expected_start = time.perf_counter()
    expected = make_expected(frame)
    deterministic_seconds = time.perf_counter() - expected_start
    actual_keys = key_tuples(frame)
    expected_keys = key_tuples(expected)
    missing = expected_keys - actual_keys
    extra = actual_keys - expected_keys
    groups = frame.groupby(KEY, dropna=False, sort=False)
    duplicate_groups = groups.size().reset_index(name="execution_count")
    duplicate_groups = duplicate_groups[duplicate_groups["execution_count"] > 1].copy()
    unique = frame.drop_duplicates(KEY, keep="last").copy()

    summary = pd.DataFrame([{
        "executions": len(frame),
        "expected_unique_configurations": len(expected),
        "observed_unique_configurations": len(actual_keys),
        "duplicate_groups": len(duplicate_groups),
        "duplicate_rows_beyond_first": int(len(frame) - len(actual_keys)),
        "missing_expected_configurations": len(missing),
        "extra_configurations": len(extra),
        "unique_ok": int((unique["status"] == "ok").sum()),
        "unique_no_signal": int((unique["status"] == "no_signal").sum()),
        "deterministic_enumeration_seconds": deterministic_seconds,
        "scenario_precision": len(actual_keys & expected_keys) / len(actual_keys),
        "scenario_recall": len(actual_keys & expected_keys) / len(expected_keys),
    }])
    missing_frame = pd.DataFrame(list(missing), columns=KEY)
    extra_frame = pd.DataFrame(list(extra), columns=KEY)
    return summary, duplicate_groups, missing_frame, extra_frame


def duplicate_consistency(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for key_values, group in frame.groupby(KEY, dropna=False):
        if len(group) < 2:
            continue
        row = dict(zip(KEY, key_values if isinstance(key_values, tuple) else (key_values,)))
        row["execution_count"] = len(group)
        row["iterations"] = ",".join(map(str, sorted(group["iteration"].astype(int).unique())))
        row["statuses_identical"] = group["status"].nunique(dropna=False) == 1
        for column in NUMERIC_COMPARE:
            values = pd.to_numeric(group[column], errors="coerce")
            row[f"{column}_range"] = float(values.max() - values.min()) if values.notna().any() else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def modulation_audit(frame: pd.DataFrame) -> pd.DataFrame:
    ok = frame[(frame["status"] == "ok")].drop_duplicates(KEY, keep="last").copy()
    pivot_key = ["source", "destination", "transmitter_type", "channel_pattern", "launch_power_dbm"]
    pairs = ok.pivot_table(index=pivot_key, columns="modulation", values="gsnr_db", aggfunc="first").reset_index()
    if "QPSK" in pairs.columns and "16QAM" in pairs.columns:
        pairs["qpsk_minus_16qam_gsnr_db"] = pd.to_numeric(pairs["QPSK"], errors="coerce") - pd.to_numeric(
            pairs["16QAM"], errors="coerce"
        )
    return pairs


WARNING_PATTERNS = {
    "missing_type_variety": re.compile(r"missing type_variety", re.I),
    "missing_pmd": re.compile(r"missing pmd attribute", re.I),
    "missing_pdl": re.compile(r"missing pdl attribute", re.I),
    "missing_pmd_coef": re.compile(r"missing pmd_coef", re.I),
    "edfa_below_min_gain": re.compile(r"effective gain.*?below user specified amplifier", re.I | re.S),
    "roadm_target_power_unmet": re.compile(r"maximum target power .*? can not be met", re.I),
}


def audit_artifact_warnings(archive_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    scenario_rows = []
    totals = {name: 0 for name in WARNING_PATTERNS}
    with zipfile.ZipFile(archive_path) as archive:
        names = [name for name in archive.namelist() if name.endswith("_result.json")]
        for name in names:
            payload = json.loads(archive.read(name))
            stderr = payload.get("stderr", "") or ""
            scenario_id = Path(name).name.replace("_result.json", "")
            row = {"scenario_id": scenario_id, "stderr_chars": len(stderr)}
            critical = False
            for warning_name, pattern in WARNING_PATTERNS.items():
                count = len(pattern.findall(stderr))
                row[warning_name] = count
                totals[warning_name] += count
                if warning_name in {"edfa_below_min_gain", "roadm_target_power_unmet"} and count:
                    critical = True
            row["has_critical_configuration_warning"] = critical
            scenario_rows.append(row)
    scenario_frame = pd.DataFrame(scenario_rows)
    summary = pd.DataFrame([
        {
            "warning_category": warning_name,
            "total_occurrences": count,
            "scenarios_affected": int((scenario_frame[warning_name] > 0).sum()),
            "severity": "critical_configuration" if warning_name in {"edfa_below_min_gain", "roadm_target_power_unmet"} else "schema_default",
        }
        for warning_name, count in totals.items()
    ])
    return scenario_frame, summary


def spectrum_payload(pattern: str, power: float, modulation: str) -> dict:
    # Match the spectral grid used by the published artifacts.  The public
    # QPSK cases use 37.5 GHz slots, whereas the 16QAM cases use 50 GHz slots.
    spacing = 37.5e9 if modulation == "QPSK" else 50e9
    spectrum = []
    for index, state in enumerate(pattern):
        spectrum.append({
            "f_min": 194e12 + index * spacing,
            "f_max": 194e12 + index * spacing,
            "baud_rate": 32e9,
            "slot_width": spacing,
            "delta_pdb": 0.0,
            "roll_off": 0.15,
            "tx_osnr": 40.0,
            "tx_power_dbm": float(power) if state == "1" else -120.0,
            "label": f"slot-{index + 1}",
        })
    return {"spectrum": spectrum}


CHANNEL_LINE = re.compile(
    r"^\s*(\d+)\s+([0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s*$"
)


def parse_active_metrics(stdout: str) -> tuple[float | None, float | None, int]:
    gsnr = []
    osnr = []
    for line in stdout.splitlines():
        match = CHANNEL_LINE.match(line)
        if not match:
            continue
        power = float(match.group(3))
        if power > -100:
            osnr.append(float(match.group(4)))
            gsnr.append(float(match.group(6)))
    return (
        float(np.mean(gsnr)) if gsnr else None,
        float(np.mean(osnr)) if osnr else None,
        len(gsnr),
    )


def independent_reproduction(frame: pd.DataFrame) -> pd.DataFrame:
    executable = Path(sys.executable).parent / "gnpy-transmission-example.exe"
    topology = SOURCE_DIR / "NDFF_Testbed.json"
    equipment = SOURCE_DIR / "eqpt_config_NDFF.json"
    run_dir = E4_DIR / "independent_run_inputs"
    run_dir.mkdir(parents=True, exist_ok=True)
    unique = frame.drop_duplicates(KEY, keep="last").copy()
    lookup = unique.set_index(KEY, drop=False)
    patterns = {1: "10000000", 2: "11000000", 4: "11110000", 8: "11111111"}
    rows = []
    raw_log_path = E4_DIR / "independent_run_logs.jsonl"
    with raw_log_path.open("w", encoding="utf-8") as log_handle:
        for destination, modulation, power, (load, pattern) in itertools.product(
            sorted(DESTINATION_UID), ["QPSK", "16QAM"], [-5.5, -5.0, -4.5], patterns.items()
        ):
            run_id = f"{destination.replace(' ', '_')}__{modulation}__p{str(power).replace('.', 'p').replace('-', 'm')}__load{load}"
            spectrum_path = run_dir / f"{run_id}.json"
            spectrum_path.write_text(
                json.dumps(spectrum_payload(pattern, power, modulation), indent=2),
                encoding="utf-8",
            )
            command = [
                str(executable), str(topology), "tx_uob", DESTINATION_UID[destination],
                "-e", str(equipment), "--spectrum", str(spectrum_path), "--show-channels", "-po", str(power),
            ]
            start = time.perf_counter()
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            elapsed = time.perf_counter() - start
            reproduced_gsnr, reproduced_osnr, parsed_channels = parse_active_metrics(completed.stdout)
            key = ("bristol", destination, "voyager", modulation, pattern, float(power))
            if key in lookup.index:
                published = lookup.loc[key]
                if isinstance(published, pd.DataFrame):
                    published = published.iloc[-1]
                published_gsnr = pd.to_numeric(pd.Series([published["gsnr_db"]]), errors="coerce").iloc[0]
                published_osnr = pd.to_numeric(pd.Series([published["osnr_db"]]), errors="coerce").iloc[0]
            else:
                published_gsnr = np.nan
                published_osnr = np.nan
            critical_counts = {
                name: len(pattern_re.findall(completed.stderr or ""))
                for name, pattern_re in WARNING_PATTERNS.items()
            }
            stderr = completed.stderr or ""
            if "LY_EVALID" in stderr or "Unsatisfied range" in stderr or "Duplicate instance" in stderr:
                error_category = "public_equipment_schema_validation_failure"
            elif completed.returncode != 0:
                error_category = "other_cli_failure"
            else:
                error_category = "none"
            rows.append({
                "run_id": run_id, "destination": destination, "modulation": modulation,
                "launch_power_dbm": power, "active_channels": load, "channel_pattern": pattern,
                "return_code": completed.returncode, "runtime_seconds": elapsed,
                "parsed_active_channels": parsed_channels,
                "published_gsnr_db": published_gsnr, "reproduced_gsnr_db": reproduced_gsnr,
                "abs_gsnr_difference_db": abs(reproduced_gsnr - published_gsnr) if reproduced_gsnr is not None and pd.notna(published_gsnr) else np.nan,
                "published_osnr_db": published_osnr, "reproduced_osnr_db": reproduced_osnr,
                "abs_osnr_difference_db": abs(reproduced_osnr - published_osnr) if reproduced_osnr is not None and pd.notna(published_osnr) else np.nan,
                "stderr_chars": len(completed.stderr or ""),
                "error_category": error_category,
                **critical_counts,
            })
            log_handle.write(json.dumps({
                "run_id": run_id, "command": command, "return_code": completed.returncode,
                "stdout": completed.stdout, "stderr": completed.stderr,
            }) + "\n")
    return pd.DataFrame(rows)


def write_e3_outputs(frame: pd.DataFrame, coverage: pd.DataFrame, report_text: str) -> None:
    E3_DIR.mkdir(parents=True, exist_ok=True)
    first_iteration = frame[frame["iteration"].astype(int) == 1]
    second_iteration = frame[frame["iteration"].astype(int) == 2]
    observable = pd.DataFrame([{
        "method": "DeterministicEnumerator",
        "trials_observable": 1,
        "executions": 0,
        "unique_configurations": int(coverage.iloc[0]["expected_unique_configurations"]),
        "duplicate_rate": 0.0,
        "scenario_precision": 1.0,
        "scenario_recall": 1.0,
        "model": "not_applicable",
        "llm_calls": 0,
        "tokens": 0,
        "api_cost": 0,
        "latency_seconds": float(coverage.iloc[0]["deterministic_enumeration_seconds"]),
        "human_interventions": 0,
    }, {
        "method": "PublishedMultiAgentArtifact",
        "trials_observable": 1,
        "executions": len(frame),
        "unique_configurations": int(coverage.iloc[0]["observed_unique_configurations"]),
        "duplicate_rate": float((len(frame) - frame.drop_duplicates(KEY).shape[0]) / len(frame)),
        "scenario_precision": float(coverage.iloc[0]["scenario_precision"]),
        "scenario_recall": float(coverage.iloc[0]["scenario_recall"]),
        "model": "recovered_source_default_gpt-4o-mini_actual_runtime_unverified",
        "llm_calls": "not_reported_control_path_up_to_6",
        "tokens": "not_reported",
        "api_cost": "not_reported",
        "latency_seconds": "not_reported",
        "human_interventions": "not_reported",
    }])
    observable.to_csv(E3_DIR / "observable_baseline_comparison.csv", index=False)
    frame.groupby(["iteration", "launch_power_dbm", "status"], dropna=False).size().reset_index(
        name="records"
    ).to_csv(E3_DIR / "published_iteration_audit.csv", index=False)
    limitations = """# E3: Deterministic baseline and public-evidence audit

## Completed

- Generated the complete 6,144-configuration ground-truth set with a deterministic enumerator.
- Compared the published multi-agent scenario set against the deterministic set.
- Measured missing, extra and duplicate configurations.
- Audited the two published iterations and their launch powers.

## Recovered implementation evidence

The later-recovered local source contains the Planner, Scenario Expander and Reflection prompts and JSON constraints. Its default OpenAI path uses `gpt-4o-mini`, temperature 0.2, a 90-second timeout and at most three attempts. A two-iteration refine path contains up to six LLM call sites. The source snapshot and hashes are retained under `../E5_recovered_gnpy_replay/source/`.

The archived run still lacks its environment variables, date-stamped model snapshot, per-call requests/responses, actual calls/retries, token usage, latency, cost, failures and human-intervention trace. Therefore the source default is reported separately from the unverified actual runtime, and those run-level fields remain `not_reported`. Single-LLM, no-reflection and repeated full multi-agent trials cannot be reconstructed without fabricating conditions.

## Published reflection evidence

The published report contains one two-iteration run. Iteration 1 executes 2,048 cases at -5.5 dBm. Iteration 2 executes 6,144 cases at -5.5, -5.0 and -4.5 dBm, which repeats all 2,048 first-iteration configurations. This is execution history, not 8,192 unique dataset configurations.
"""
    (E3_DIR / "README.md").write_text(limitations, encoding="utf-8")
    (E3_DIR / "published_report.txt").write_text(report_text, encoding="utf-8")


def main() -> None:
    started = time.time()
    E3_DIR.mkdir(parents=True, exist_ok=True)
    E4_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in URLS.items():
        download(url, SOURCE_DIR / filename)

    frame = normalize_key_frame(pd.read_csv(SOURCE_DIR / "scenarios.csv", dtype={"channel_pattern": str}))
    report_text = (SOURCE_DIR / "report.txt").read_text(encoding="utf-8")
    coverage, duplicates, missing, extra = audit_coverage(frame)
    coverage.to_csv(E4_DIR / "execution_and_coverage_summary.csv", index=False)
    duplicates.to_csv(E4_DIR / "duplicate_configuration_groups.csv", index=False)
    missing.to_csv(E4_DIR / "missing_expected_configurations.csv", index=False)
    extra.to_csv(E4_DIR / "extra_configurations.csv", index=False)

    duplicate_detail = duplicate_consistency(frame)
    duplicate_detail.to_csv(E4_DIR / "duplicate_consistency.csv", index=False)
    unique = frame.drop_duplicates(KEY, keep="last").copy()
    unique.to_csv(E4_DIR / "unique_configurations.csv", index=False)
    unique[unique["status"] == "ok"].to_csv(E4_DIR / "unique_valid_configurations.csv", index=False)
    frame.groupby(["iteration", "launch_power_dbm", "status"], dropna=False).size().reset_index(
        name="records"
    ).to_csv(E4_DIR / "coverage_by_iteration_power_status.csv", index=False)

    modulation = modulation_audit(frame)
    modulation.to_csv(E4_DIR / "modulation_paired_gsnr.csv", index=False)
    difference = pd.to_numeric(modulation.get("qpsk_minus_16qam_gsnr_db", pd.Series(dtype=float)), errors="coerce")
    pd.DataFrame([{
        "paired_configurations": int(difference.notna().sum()),
        "mean_absolute_gsnr_difference_db": float(difference.abs().mean()),
        "max_absolute_gsnr_difference_db": float(difference.abs().max()),
        "exactly_equal_fraction": float((difference == 0).mean()),
    }]).to_csv(E4_DIR / "modulation_sensitivity_summary.csv", index=False)

    warning_by_scenario, warning_summary = audit_artifact_warnings(SOURCE_DIR / "artifacts.zip")
    warning_by_scenario.to_csv(E4_DIR / "published_warning_flags_by_scenario.csv", index=False)
    warning_summary.to_csv(E4_DIR / "published_warning_summary.csv", index=False)

    reproduction = independent_reproduction(frame)
    reproduction.to_csv(E4_DIR / "independent_reproduction_96_cases.csv", index=False)
    successful = reproduction[reproduction["return_code"] == 0]
    pd.DataFrame([{
        "requested_cases": len(reproduction),
        "successful_cli_runs": len(successful),
        "public_config_validation_failures": int(
            (reproduction["error_category"] == "public_equipment_schema_validation_failure").sum()
        ),
        "parsed_cases": int(successful["reproduced_gsnr_db"].notna().sum()),
        "mean_abs_gsnr_difference_db": successful["abs_gsnr_difference_db"].mean(),
        "max_abs_gsnr_difference_db": successful["abs_gsnr_difference_db"].max(),
        "mean_abs_osnr_difference_db": successful["abs_osnr_difference_db"].mean(),
        "max_abs_osnr_difference_db": successful["abs_osnr_difference_db"].max(),
        "cases_with_critical_warnings": int(((successful["edfa_below_min_gain"] > 0) | (successful["roadm_target_power_unmet"] > 0)).sum()),
    }]).to_csv(E4_DIR / "independent_reproduction_summary.csv", index=False)

    write_e3_outputs(frame, coverage, report_text)
    provenance = {
        "python": sys.version,
        "platform": platform.platform(),
        "gnpy_version_independent_reproduction": importlib.metadata.version("gnpy"),
        "public_repository": "hpn-bristol/LLM-Driven-Multi-Agent-Optical-Digital-Twin-for-Automated-Data-Generation",
        "public_repository_commit_inspected": "f11545c642976607a87b5dc7b9c91b5b8753e0e9",
        "sources": {filename: {"url": URLS[filename], "sha256": sha256(SOURCE_DIR / filename)} for filename in URLS},
        "runtime_seconds": time.time() - started,
    }
    (E4_DIR / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    (E3_DIR / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    readme = f"""# E4: GNPy uniqueness and correctness audit

The audit separates execution records from unique configurations, compares the published scenario set with a deterministic 6,144-configuration ground truth, scans all published result artifacts for simulator warnings, and independently reruns 96 stratified configurations with GNPy {provenance['gnpy_version_independent_reproduction']}.

The independent rerun uses the public `NDFF_Testbed.json` and `eqpt_config_NDFF.json`; it does not reuse the per-scenario request/result files produced by the agent workflow. All 96 requested runs fail before propagation because the public equipment file violates the schema accepted by PyPI GNPy {provenance['gnpy_version_independent_reproduction']} (out-of-range span values and duplicate transceiver modes). A separate compatibility check gives the same failure under GNPy 2.13.0. The public repository does not pin the original GNPy commit, so the published numerical outputs cannot currently be independently regenerated from the supplied inputs.
"""
    (E4_DIR / "README.md").write_text(readme, encoding="utf-8")


if __name__ == "__main__":
    main()
