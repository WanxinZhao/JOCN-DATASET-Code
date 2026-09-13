"""Replay the stratified GNPy audit with a pinned historical core and a strict core.

The historical arm uses official GNPy v2.12 at commit
7ce665010970f57e46672db1ec0864ae448077e6.  This is the newest tagged
pre-YANG release that accepts the archived legacy equipment and spectrum
documents without changing their semantics.  The strict arm uses GNPy 2.14.2,
a schema-corrected equipment copy, and active-channel-only spectra.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
E4 = ROOT / "MajorRevision" / "E4_gnpy_audit"
E5 = ROOT / "MajorRevision" / "E5_recovered_gnpy_replay"
SOURCE = E5 / "source"
INPUTS = E5 / "inputs"
HISTORICAL_LOG = E5 / "historical_v2.12_logs.jsonl"
STRICT_LOG = E5 / "strict_v2.14.2_logs.jsonl"
PINNED_GNPY_COMMIT = "7ce665010970f57e46672db1ec0864ae448077e6"
DESTINATION_UID = {
    "bradley stoke": "node_brd",
    "froxfield": "node_ffd",
    "powergate": "node_pgt",
    "reading": "node_rdg",
}
CHANNEL_LINE = re.compile(
    r"^\s*(\d+)\s+([0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s*$"
)
WARNING_PATTERNS = {
    "edfa_below_min_gain": re.compile(r"effective gain.*?below user specified amplifier", re.I | re.S),
    "roadm_target_power_unmet": re.compile(r"maximum target power .*? can not be met", re.I),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def normalize_pattern(value: str) -> str:
    return str(value).strip().zfill(8)


def scenario_key(row: dict[str, str]) -> tuple[str, str, str, str, str, float]:
    return (
        str(row.get("source", "bristol")).strip().lower(),
        str(row["destination"]).strip().lower(),
        str(row.get("transmitter_type", "voyager")).strip().lower(),
        str(row["modulation"]).strip(),
        normalize_pattern(row["channel_pattern"]),
        float(row["launch_power_dbm"]),
    )


def audit_key(row: dict[str, str]) -> tuple[str, str, str, str, str, float]:
    return (
        "bristol",
        str(row["destination"]).strip().lower(),
        "voyager",
        str(row["modulation"]).strip(),
        normalize_pattern(row["channel_pattern"]),
        float(row["launch_power_dbm"]),
    )


def parse_metrics(stdout: str) -> tuple[float | None, float | None, int]:
    gsnr: list[float] = []
    osnr: list[float] = []
    for line in stdout.splitlines():
        match = CHANNEL_LINE.match(line)
        if not match:
            continue
        power = float(match.group(3))
        # Real channels arrive near 0 dBm. Historical off-slot placeholders
        # arrive near -114 dBm; the strict single-channel guard arrives near
        # -34 dBm.  A -20 dBm threshold excludes both without affecting data.
        if power > -20:
            osnr.append(float(match.group(4)))
            gsnr.append(float(match.group(6)))
    return (
        statistics.fmean(gsnr) if gsnr else None,
        statistics.fmean(osnr) if osnr else None,
        len(gsnr),
    )


def archived_metrics(payload: dict) -> tuple[float | None, float | None]:
    metrics = payload.get("parsed_output", {}).get("metrics", {})
    return metrics.get("gsnr_signal_bw_db"), metrics.get("osnr_signal_bw_db")


def numeric_difference(first: float | None, second: float | None) -> float | None:
    if first is None or second is None:
        return None
    return abs(float(first) - float(second))


def warning_counts(stderr: str) -> dict[str, int]:
    return {name: len(pattern.findall(stderr or "")) for name, pattern in WARNING_PATTERNS.items()}


def historical_command(python: Path, checkout: Path, topology: Path, equipment: Path,
                       spectrum: Path, destination: str, power: float) -> list[str]:
    launcher = (
        "import sys; "
        f"sys.path.insert(0, r'{checkout}'); "
        "from gnpy.tools.cli_examples import transmission_main_example; "
        "transmission_main_example()"
    )
    return [
        str(python), "-c", launcher, str(topology), "tx_uob", DESTINATION_UID[destination],
        "-e", str(equipment), "--spectrum", str(spectrum), "--show-channels", "-po", str(power),
    ]


def run_command(command: list[str]) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.perf_counter()
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    return completed, time.perf_counter() - started


def fixed_equipment(original: dict) -> tuple[dict, list[dict]]:
    fixed = json.loads(json.dumps(original))
    changes: list[dict] = []
    span = fixed["Span"][0]
    for field, replacement, rationale in [
        ("target_extended_gain", 2.5, "GNPy reference default and YANG range-compliant"),
        ("max_loss", 28, "GNPy reference default and YANG range-compliant"),
    ]:
        changes.append({"path": f"Span[0].{field}", "old": span[field], "new": replacement,
                        "rationale": rationale})
        span[field] = replacement
    for transceiver in fixed["Transceiver"]:
        if transceiver.get("type_variety") != "teraflex":
            continue
        kept = []
        seen = set()
        for mode in transceiver.get("mode", []):
            if mode["format"] in seen:
                changes.append({
                    "path": "Transceiver[teraflex].mode",
                    "old": f"{mode['format']}@{mode['baud_rate']}",
                    "new": "removed",
                    "rationale": "YANG keys mode by format; unused duplicate 64-Gbaud mode",
                })
                continue
            seen.add(mode["format"])
            kept.append(mode)
        transceiver["mode"] = kept
    return fixed, changes


def active_only_spectrum(payload: dict) -> dict:
    active = [json.loads(json.dumps(item)) for item in payload["spectrum"]
              if float(item.get("tx_power_dbm", -120)) > -100]
    if len(active) == 1:
        # GNPy 2.14.2's EDFA interpolation assumes at least two frequencies.
        # Add one schema-valid, negligible-power guard solely for that edge case.
        guard = next(json.loads(json.dumps(item)) for item in payload["spectrum"]
                     if float(item.get("tx_power_dbm", -120)) <= -100)
        guard["tx_power_dbm"] = -40.0
        guard["label"] = str(guard.get("label", "guard")) + "-strict-guard"
        active.append(guard)
        active.sort(key=lambda item: float(item["f_min"]))
    return {"spectrum": active}


def ensure_pinned_checkout(checkout: Path) -> None:
    if not (checkout / ".git").exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "v2.12",
             "https://github.com/Telecominfraproject/oopt-gnpy.git", str(checkout)],
            check=True,
        )
    actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if actual != PINNED_GNPY_COMMIT:
        raise RuntimeError(f"Expected GNPy {PINNED_GNPY_COMMIT}, found {actual}")


def summarize(rows: list[dict], prefix: str) -> dict:
    successes = [row for row in rows if int(row[f"{prefix}_return_code"]) == 0]
    gsnr = [float(row[f"{prefix}_abs_gsnr_difference_db"]) for row in successes
            if row[f"{prefix}_abs_gsnr_difference_db"] not in (None, "")]
    osnr = [float(row[f"{prefix}_abs_osnr_difference_db"]) for row in successes
            if row[f"{prefix}_abs_osnr_difference_db"] not in (None, "")]
    return {
        "arm": prefix,
        "requested_cases": len(rows),
        "successful_cli_runs": len(successes),
        "parsed_cases": sum(row[f"{prefix}_gsnr_db"] is not None for row in successes),
        "mean_abs_gsnr_difference_db": statistics.fmean(gsnr) if gsnr else None,
        "max_abs_gsnr_difference_db": max(gsnr) if gsnr else None,
        "mean_abs_osnr_difference_db": statistics.fmean(osnr) if osnr else None,
        "max_abs_osnr_difference_db": max(osnr) if osnr else None,
        "cases_with_gsnr_difference_le_0p0051_db": sum(value <= 0.0051 for value in gsnr),
        "cases_with_critical_warnings": sum(
            int(row[f"{prefix}_edfa_below_min_gain"]) > 0
            or int(row[f"{prefix}_roadm_target_power_unmet"]) > 0
            for row in successes
        ),
    }


def grouped_differences(rows: list[dict]) -> list[dict]:
    output = []
    for arm in ("historical", "strict"):
        for factor in ("destination", "modulation", "launch_power_dbm", "active_channels"):
            levels = sorted({str(row[factor]) for row in rows})
            for level in levels:
                group = [row for row in rows if str(row[factor]) == level]
                gsnr = [float(row[f"{arm}_abs_gsnr_difference_db"]) for row in group
                        if row[f"{arm}_abs_gsnr_difference_db"] not in (None, "")]
                osnr = [float(row[f"{arm}_abs_osnr_difference_db"]) for row in group
                        if row[f"{arm}_abs_osnr_difference_db"] not in (None, "")]
                output.append({
                    "arm": arm, "factor": factor, "level": level, "cases": len(group),
                    "mean_abs_gsnr_difference_db": statistics.fmean(gsnr),
                    "max_abs_gsnr_difference_db": max(gsnr),
                    "mean_abs_osnr_difference_db": statistics.fmean(osnr),
                    "max_abs_osnr_difference_db": max(osnr),
                })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-python", type=Path, default=ROOT / ".runtime" / "python39" / "python.exe")
    parser.add_argument("--historical-checkout", type=Path, default=ROOT / ".runtime" / "gnpy-v2.12-src")
    parser.add_argument(
        "--strict-cli", type=Path,
        default=Path.home() / "AppData/Local/Temp/jocn-majorrevision-venv/Scripts/gnpy-transmission-example.exe",
    )
    args = parser.parse_args()
    E5.mkdir(parents=True, exist_ok=True)
    SOURCE.mkdir(parents=True, exist_ok=True)
    INPUTS.mkdir(parents=True, exist_ok=True)
    ensure_pinned_checkout(args.historical_checkout.resolve())
    if not args.historical_python.exists():
        raise FileNotFoundError(f"Historical Python not found: {args.historical_python}")
    if not args.strict_cli.exists():
        raise FileNotFoundError(f"Strict GNPy CLI not found: {args.strict_cli}")

    topology = SOURCE / "NDFF_Testbed.json"
    original_equipment = SOURCE / "eqpt_config_NDFF_original.json"
    fixed_equipment_path = SOURCE / "eqpt_config_NDFF_schema_fixed.json"
    archive_path = E4 / "source" / "artifacts.zip"
    scenarios_path = E4 / "source" / "scenarios.csv"
    selections_path = E4 / "independent_reproduction_96_cases.csv"
    if not topology.exists():
        shutil.copy2(E4 / "source" / "NDFF_Testbed.json", topology)
    if not original_equipment.exists():
        shutil.copy2(E4 / "source" / "eqpt_config_NDFF.json", original_equipment)
    fixed_payload, changes = fixed_equipment(json.loads(original_equipment.read_text(encoding="utf-8")))
    fixed_equipment_path.write_text(json.dumps(fixed_payload, indent=2) + "\n", encoding="utf-8")
    (E5 / "configuration_changes.json").write_text(json.dumps(changes, indent=2) + "\n", encoding="utf-8")

    scenario_lookup = {}
    for row in read_csv(scenarios_path):
        scenario_lookup[scenario_key(row)] = row
    selections = read_csv(selections_path)
    results: list[dict] = []
    started = time.time()
    with zipfile.ZipFile(archive_path) as archive, \
            HISTORICAL_LOG.open("w", encoding="utf-8") as historical_log, \
            STRICT_LOG.open("w", encoding="utf-8") as strict_log:
        for index, selection in enumerate(selections, 1):
            key = audit_key(selection)
            scenario = scenario_lookup[key]
            scenario_id = scenario["scenario_id"]
            member_root = f"artifacts/{scenario_id}/{scenario_id}"
            spectrum_payload = json.loads(archive.read(member_root + "_spectrum.json"))
            result_payload = json.loads(archive.read(member_root + "_result.json"))
            historical_spectrum = INPUTS / f"{selection['run_id']}__archived.json"
            strict_spectrum = INPUTS / f"{selection['run_id']}__active_only.json"
            historical_spectrum.write_text(json.dumps(spectrum_payload, indent=2) + "\n", encoding="utf-8")
            strict_spectrum.write_text(json.dumps(active_only_spectrum(spectrum_payload), indent=2) + "\n", encoding="utf-8")
            power = float(selection["launch_power_dbm"])
            destination = selection["destination"].strip().lower()

            historical_cmd = historical_command(
                args.historical_python.resolve(), args.historical_checkout.resolve(), topology.resolve(),
                original_equipment.resolve(), historical_spectrum.resolve(), destination, power,
            )
            historical_completed, historical_seconds = run_command(historical_cmd)
            historical_gsnr, historical_osnr, historical_channels = parse_metrics(historical_completed.stdout)
            archived_gsnr, archived_osnr = archived_metrics(result_payload)
            h_warnings = warning_counts(historical_completed.stderr)
            historical_log.write(json.dumps({
                "run_id": selection["run_id"], "scenario_id": scenario_id, "command": historical_cmd,
                "return_code": historical_completed.returncode, "stdout": historical_completed.stdout,
                "stderr": historical_completed.stderr,
            }) + "\n")

            strict_cmd = [
                str(args.strict_cli.resolve()), str(topology.resolve()), "tx_uob", DESTINATION_UID[destination],
                "-e", str(fixed_equipment_path.resolve()), "--spectrum", str(strict_spectrum.resolve()),
                "--show-channels", "-po", str(power),
            ]
            strict_completed, strict_seconds = run_command(strict_cmd)
            strict_gsnr, strict_osnr, strict_channels = parse_metrics(strict_completed.stdout)
            s_warnings = warning_counts(strict_completed.stderr)
            strict_log.write(json.dumps({
                "run_id": selection["run_id"], "scenario_id": scenario_id, "command": strict_cmd,
                "return_code": strict_completed.returncode, "stdout": strict_completed.stdout,
                "stderr": strict_completed.stderr,
            }) + "\n")

            row = {
                "run_id": selection["run_id"], "scenario_id": scenario_id,
                "destination": destination, "modulation": selection["modulation"],
                "launch_power_dbm": power, "active_channels": int(selection["active_channels"]),
                "channel_pattern": normalize_pattern(selection["channel_pattern"]),
                "strict_guard_channels": 1 if int(selection["active_channels"]) == 1 else 0,
                "archived_gsnr_db": archived_gsnr, "archived_osnr_db": archived_osnr,
                "historical_return_code": historical_completed.returncode,
                "historical_runtime_seconds": historical_seconds,
                "historical_parsed_active_channels": historical_channels,
                "historical_gsnr_db": historical_gsnr, "historical_osnr_db": historical_osnr,
                "historical_abs_gsnr_difference_db": numeric_difference(historical_gsnr, archived_gsnr),
                "historical_abs_osnr_difference_db": numeric_difference(historical_osnr, archived_osnr),
                "historical_edfa_below_min_gain": h_warnings["edfa_below_min_gain"],
                "historical_roadm_target_power_unmet": h_warnings["roadm_target_power_unmet"],
                "strict_return_code": strict_completed.returncode,
                "strict_runtime_seconds": strict_seconds,
                "strict_parsed_active_channels": strict_channels,
                "strict_gsnr_db": strict_gsnr, "strict_osnr_db": strict_osnr,
                "strict_abs_gsnr_difference_db": numeric_difference(strict_gsnr, archived_gsnr),
                "strict_abs_osnr_difference_db": numeric_difference(strict_osnr, archived_osnr),
                "strict_edfa_below_min_gain": s_warnings["edfa_below_min_gain"],
                "strict_roadm_target_power_unmet": s_warnings["roadm_target_power_unmet"],
            }
            results.append(row)
            print(f"[{index:02d}/{len(selections)}] {selection['run_id']}: "
                  f"historical={historical_completed.returncode}, strict={strict_completed.returncode}", flush=True)

    write_csv(E5 / "replay_results_96_cases.csv", results)
    summaries = [summarize(results, "historical"), summarize(results, "strict")]
    write_csv(E5 / "replay_summary.csv", summaries)
    write_csv(E5 / "difference_by_factor.csv", grouped_differences(results))
    freeze = subprocess.check_output(
        [str(args.historical_python.resolve()), "-m", "pip", "freeze"], text=True, encoding="utf-8", errors="replace"
    )
    (E5 / "requirements-historical-python39-freeze.txt").write_text(freeze, encoding="utf-8")
    strict_python = args.strict_cli.resolve().parent / "python.exe"
    strict_freeze = subprocess.check_output(
        [str(strict_python), "-m", "pip", "freeze"], text=True, encoding="utf-8", errors="replace"
    )
    (E5 / "requirements-strict-python312-freeze.txt").write_text(strict_freeze, encoding="utf-8")
    provenance = {
        "created_unix_time": time.time(), "runtime_seconds": time.time() - started,
        "platform": platform.platform(), "driver_python": sys.version,
        "historical_python": subprocess.check_output([str(args.historical_python.resolve()), "--version"], text=True).strip(),
        "historical_gnpy_tag": "v2.12", "historical_gnpy_commit": PINNED_GNPY_COMMIT,
        "strict_gnpy_version": "2.14.2", "strict_libyang_version": "0.0.14",
        "files": {
            label: {"path": display_path, "sha256": sha256(path), "bytes": path.stat().st_size}
            for label, (display_path, path) in {
                "topology": ("source/NDFF_Testbed.json", topology),
                "recovered_original_equipment": ("source/eqpt_config_NDFF_original.json", original_equipment),
                "schema_fixed_equipment": ("source/eqpt_config_NDFF_schema_fixed.json", fixed_equipment_path),
                "published_artifacts_archive": ("../E4_gnpy_audit/source/artifacts.zip", archive_path),
                "published_scenarios": ("../E4_gnpy_audit/source/scenarios.csv", scenarios_path),
            }.items()
        },
        "interpretation": (
            "v2.12 is an exact-output compatibility reconstruction, not proof that the original unrecorded "
            "runtime commit was v2.12. The strict arm changes invalid configuration fields and removes off-channel "
            "-120 dBm placeholders, so it is an independent correctness/sensitivity check rather than a bitwise replay."
        ),
    }
    (E5 / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
