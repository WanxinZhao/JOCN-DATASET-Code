import csv
import io
from typing import Any, Dict, List


class ReportAgent:
    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _display_status(self, value: Any) -> str:
        text = str(value or "unknown")
        if text == "not_applicable":
            return "no_signal"
        return text

    def _record_sort_key(self, record: Dict[str, Any]) -> tuple[Any, ...]:
        scenario = record["scenario"]
        modulation_order = {"QPSK": 0, "16QAM": 1, "64QAM": 2}
        return (
            modulation_order.get(str(scenario.get("modulation", "")).upper(), 99),
            str(scenario.get("transmitter_type", "")),
            self._safe_float(scenario.get("baud_rate_gbaud", 0.0)),
            str(scenario.get("source", "")),
            str(scenario.get("destination", "")),
            str(scenario.get("channel_pattern", "")),
            self._safe_float(scenario.get("power", 0.0)),
            str(scenario.get("scenario_id", "")),
        )

    def build_csv(
        self,
        records: List[Dict[str, Any]],
        feasibility_profiles: List[Dict[str, Any]] | None = None,
    ) -> str:
        _ = feasibility_profiles
        fieldnames = [
            "scenario_id",
            "iteration",
            "network",
            "source",
            "destination",
            "target_date",
            "target_column",
            "distance_km",
            "transmitter_type",
            "modulation",
            "baud_rate_gbaud",
            "bit_rate_gbps",
            "channel_slots",
            "channel_pattern",
            "channels",
            "launch_power_dbm",
            "preferred_engine",
            "selected_engine",
            "decision_reason",
            "backend",
            "status",
            "gsnr_db",
            "osnr_db",
            "ase_dbm",
            "gsnr_signal_bw_db",
            "gsnr_0p1nm_db",
            "osnr_signal_bw_db",
            "osnr_0p1nm_db",
            "ase_signal_bw_dbm",
            "ase_0p1nm_dbm",
            "rx_total_wdm_power_dbm",
            "rx_signal_power_dbm",
            "effective_snr_db",
            "ber",
            "r2",
            "mae",
            "mse",
            "evm",
            "gmi",
            "ngmi",
            "error_message",
            "path_nodes",
            "span_count",
            "gnpy_request_json",
            "gnpy_result_json",
            "gnpy_spectrum_json",
            "opticommpy_request_json",
            "opticommpy_result_json",
            "lstm_request_json",
            "lstm_result_json",
            "lstm_forecast_csv",
            "lstm_model_path",
            "eye_diagram_png",
            "constellation_png",
            "psd_png",
            "notes",
            "metrics_json",
        ]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for record in sorted(records, key=self._record_sort_key):
            scenario = record["scenario"]
            output = record["output"]
            metrics = output.get("metrics", {})
            artifacts = output.get("artifacts", {})
            writer.writerow(
                {
                    "scenario_id": scenario.get("scenario_id"),
                    "iteration": scenario.get("iteration"),
                    "network": scenario.get("network"),
                    "source": scenario.get("source"),
                    "destination": scenario.get("destination"),
                    "target_date": scenario.get("target_date"),
                    "target_column": scenario.get("target_column"),
                    "distance_km": scenario.get("distance"),
                    "transmitter_type": scenario.get("transmitter_type"),
                    "modulation": scenario.get("modulation"),
                    "baud_rate_gbaud": scenario.get("baud_rate_gbaud"),
                    "bit_rate_gbps": scenario.get("bit_rate_gbps"),
                    "channel_slots": scenario.get("channel_slots"),
                    "channel_pattern": scenario.get("channel_pattern"),
                    "channels": scenario.get("channels"),
                    "launch_power_dbm": scenario.get("power"),
                    "preferred_engine": scenario.get("preferred_engine"),
                    "selected_engine": record.get("tool"),
                    "decision_reason": record.get("reason"),
                    "backend": output.get("backend"),
                    "status": self._display_status(output.get("status")),
                    "gsnr_db": metrics.get("gsnr_signal_bw_db", metrics.get("effective_snr_db")),
                    "osnr_db": metrics.get("osnr_signal_bw_db", metrics.get("osnr_0p1nm_db", metrics.get("gn_model_osnr_db"))),
                    "ase_dbm": metrics.get("ase_signal_bw_dbm"),
                    "gsnr_signal_bw_db": metrics.get("gsnr_signal_bw_db"),
                    "gsnr_0p1nm_db": metrics.get("gsnr_0p1nm_db"),
                    "osnr_signal_bw_db": metrics.get("osnr_signal_bw_db"),
                    "osnr_0p1nm_db": metrics.get("osnr_0p1nm_db"),
                    "ase_signal_bw_dbm": metrics.get("ase_signal_bw_dbm"),
                    "ase_0p1nm_dbm": metrics.get("ase_0p1nm_dbm"),
                    "rx_total_wdm_power_dbm": metrics.get("rx_total_wdm_power_dbm"),
                    "rx_signal_power_dbm": metrics.get("rx_signal_power_dbm"),
                    "effective_snr_db": metrics.get("effective_snr_db"),
                    "ber": metrics.get("ber"),
                    "r2": metrics.get("r2"),
                    "mae": metrics.get("mae"),
                    "mse": metrics.get("mse"),
                    "evm": metrics.get("evm"),
                    "gmi": metrics.get("gmi"),
                    "ngmi": metrics.get("ngmi"),
                    "error_message": metrics.get("error_message"),
                    "path_nodes": " -> ".join(scenario.get("path_nodes", [])),
                    "span_count": len(scenario.get("spans", [])),
                    "gnpy_request_json": artifacts.get("gnpy_request_json"),
                    "gnpy_result_json": artifacts.get("gnpy_result_json"),
                    "gnpy_spectrum_json": artifacts.get("gnpy_spectrum_json"),
                    "opticommpy_request_json": artifacts.get("opticommpy_request_json"),
                    "opticommpy_result_json": artifacts.get("opticommpy_result_json"),
                    "lstm_request_json": artifacts.get("lstm_request_json"),
                    "lstm_result_json": artifacts.get("lstm_result_json"),
                    "lstm_forecast_csv": artifacts.get("lstm_forecast_csv"),
                    "lstm_model_path": artifacts.get("lstm_model_path"),
                    "eye_diagram_png": artifacts.get("eye_diagram_png"),
                    "constellation_png": artifacts.get("constellation_png"),
                    "psd_png": artifacts.get("psd_png"),
                    "notes": scenario.get("notes"),
                    "metrics_json": metrics,
                }
            )
        return stream.getvalue()

    def build_channel_csv(self, records: List[Dict[str, Any]]) -> str:
        fieldnames = [
            "scenario_id",
            "iteration",
            "network",
            "source",
            "destination",
            "transmitter_type",
            "modulation",
            "baud_rate_gbaud",
            "bit_rate_gbps",
            "launch_power_dbm",
            "channel_pattern",
            "channel_number",
            "frequency_thz",
            "channel_power_dbm",
            "osnr_signal_bw_db",
            "snr_nli_signal_bw_db",
            "gsnr_signal_bw_db",
        ]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for record in sorted(records, key=self._record_sort_key):
            scenario = record["scenario"]
            metrics = record.get("output", {}).get("metrics", {})
            channel_details = metrics.get("channel_details", [])
            if not isinstance(channel_details, list):
                continue
            for channel in channel_details:
                writer.writerow(
                    {
                        "scenario_id": scenario.get("scenario_id"),
                        "iteration": scenario.get("iteration"),
                        "network": scenario.get("network"),
                        "source": scenario.get("source"),
                        "destination": scenario.get("destination"),
                        "transmitter_type": scenario.get("transmitter_type"),
                        "modulation": scenario.get("modulation"),
                        "baud_rate_gbaud": scenario.get("baud_rate_gbaud"),
                        "bit_rate_gbps": scenario.get("bit_rate_gbps"),
                        "launch_power_dbm": scenario.get("power"),
                        "channel_pattern": scenario.get("channel_pattern"),
                        "channel_number": channel.get("channel_number"),
                        "frequency_thz": channel.get("frequency_thz"),
                        "channel_power_dbm": channel.get("channel_power_dbm"),
                        "osnr_signal_bw_db": channel.get("osnr_signal_bw_db"),
                        "snr_nli_signal_bw_db": channel.get("snr_nli_signal_bw_db"),
                        "gsnr_signal_bw_db": channel.get("gsnr_signal_bw_db"),
                    }
                )
        return stream.getvalue()

    def build_report(
        self,
        user_input: str,
        task: Dict[str, Any],
        path_bundle: Dict[str, Any],
        analysis: Dict[str, Any],
        reflection: Dict[str, Any],
        iterations: List[Dict[str, Any]],
    ) -> str:
        if task.get("task") == "time_series_generation":
            best = analysis["best_result"]
            if not best:
                return "No time-series generation result was produced."
            metrics = best["output"]["metrics"]
            artifacts = best["output"].get("artifacts", {})
            lines = [
                "Optical DT Time-Series Report",
                f"User request: {user_input}",
                f"Task: {task.get('task')} using {task.get('preferred_engine')}",
                f"Dataset file: {task.get('source')}",
                f"Target series: {metrics.get('target_column', task.get('target_column'))}",
                f"Target date: {metrics.get('target_date', task.get('target_date'))}",
                f"Time window: {metrics.get('time_window_start')} -> {metrics.get('time_window_end')}",
                f"Mode: {metrics.get('forecast_mode')}",
                f"Forecast steps: {metrics.get('forecast_steps')}",
                f"Sample interval (seconds): {metrics.get('sample_interval_seconds')}",
                f"BER mean: {metrics.get('ber_mean')}",
                f"BER min: {metrics.get('ber_min')}",
                f"BER max: {metrics.get('ber_max')}",
                f"R2: {metrics.get('r2')}",
                f"MAE: {metrics.get('mae')}",
                f"MSE: {metrics.get('mse')}",
                f"Forecast window: {metrics.get('forecast_start')} -> {metrics.get('forecast_end')}",
                f"Forecast CSV: {artifacts.get('lstm_forecast_csv')}",
                f"Saved model: {artifacts.get('lstm_model_path')}",
                f"Latest reflection: {reflection['message']}",
            ]
            return "\n".join(lines)

        best = analysis["best_result"]
        if not best:
            return "No simulation result was produced."

        primary_engine = task.get("preferred_engine")
        fallback_count = 0
        for item in analysis.get("ranked_results", []):
            record = item["record"]
            scenario_engine = record.get("scenario", {}).get("preferred_engine")
            selected_engine = record.get("tool")
            if primary_engine and scenario_engine == primary_engine and selected_engine != primary_engine:
                fallback_count += 1

        lines = [
            "Optical DT Dataset Report",
            f"User request: {user_input}",
            f"Planned task: {task['task']} on {task['network']} from {task['source']} with scope={task['scope']}",
            f"Task mode: {task.get('task_mode', 'dataset_generation')}",
            f"Primary simulator focus: {primary_engine or 'mixed'}",
            f"Resolved paths: {len(path_bundle['paths'])}",
            f"Total iterations: {len(iterations)}",
            f"Executed scenarios: {analysis['total_scenarios']}",
            f"Successful raw outputs: {analysis.get('ok_scenarios', 0)}",
            f"Execution errors: {analysis.get('error_scenarios', 0)}",
            f"No-signal patterns: {analysis.get('no_signal_scenarios', 0)}",
            f"Invalid-metric outputs: {analysis.get('invalid_metric_scenarios', 0)}",
            f"Engine counts: {analysis.get('engine_counts', {})}",
            f"Fallbacks away from primary engine: {fallback_count}",
            f"Transmitter counts: {analysis.get('transmitter_counts', {})}",
            f"Modulation counts: {analysis.get('modulation_counts', {})}",
            f"Unique channel patterns: {analysis.get('unique_channel_patterns', 0)}",
            f"Launch powers used (dBm): {analysis.get('unique_launch_powers_dbm', [])}",
            f"Representative top-recorded scenario engine: {best['tool']}",
            f"Representative top-recorded scenario: tx={best['scenario'].get('transmitter_type')}, modulation={best['scenario']['modulation']}, baud={best['scenario'].get('baud_rate_gbaud')} GBd, channels={best['scenario']['channels']}, power={best['scenario']['power']} dBm",
            f"Representative raw metrics: {best['output']['metrics']}",
            f"Latest reflection: {reflection['message']}",
            "",
            "Iteration summary:",
        ]
        for item in iterations:
            lines.append(
                f"- iteration {item['iteration']}: scenarios={item['scenario_count']}, "
                f"ok={item['analysis']['ok_scenarios']}, "
                f"errors={item['analysis']['error_scenarios']}, "
                f"no_signal={item['analysis']['no_signal_scenarios']}, "
                f"reflection={item['reflection']['action']}"
            )
        lines.append("")
        engines_used = sorted({item["record"]["tool"] for item in analysis.get("ranked_results", [])})
        lines.append(f"Engines used in executed scenarios: {', '.join(engines_used)}")
        lines.append("")
        lines.append("Representative result per path:")
        for path_key, record in analysis.get("best_per_path", {}).items():
            lines.append(
                f"- {path_key}: tx={record['scenario'].get('transmitter_type')}, "
                f"modulation={record['scenario']['modulation']}, "
                f"baud={record['scenario'].get('baud_rate_gbaud')} GBd, "
                f"pattern={record['scenario'].get('channel_pattern')}, "
                f"power={record['scenario']['power']} dBm, raw_metrics={record['output']['metrics']}"
            )
        lines.append("")
        lines.append("Recommendations:")
        for recommendation in reflection.get("recommendations", []):
            lines.append(f"- {recommendation}")
        return "\n".join(lines)
