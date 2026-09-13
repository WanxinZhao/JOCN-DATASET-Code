import json
import math
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from orchestrator.physical_layer_store import PhysicalLayerStore


class ExecutionAgent:
    def __init__(self, log_hook: Callable[[str], None] | None = None) -> None:
        self._log_hook = log_hook
        self._root = Path(__file__).resolve().parent.parent
        self._topology_path = self._root / "OFC_Testbed.json"
        self._equipment_path = self._root / "eqpt_config_NDFF.json"
        self._store = PhysicalLayerStore(
            topology_path=str(self._topology_path),
            equipment_path=str(self._equipment_path),
        )
        self._runtime_dir = self._root / "data" / "_gnpy_cli_runtime"
        self._optic_runtime_dir = self._root / "data" / "_opticommpy_runtime"
        self._topology_cache: Dict[str, Any] | None = None
        self._equipment_cache: Dict[str, Any] | None = None

    def _log(self, message: str) -> None:
        if self._log_hook:
            self._log_hook(message)

    def _load_topology_and_equipment(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if self._topology_cache is None or self._equipment_cache is None:
            self._topology_cache, self._equipment_cache = self._store.load_topology_and_equipment()
        return self._topology_cache, self._equipment_cache

    def _normalize_city(self, value: str) -> str:
        text = str(value or "").strip().lower().replace("_", " ")
        aliases = {
            "trx uob": "bristol",
            "uob": "bristol",
            "brd": "bradley stoke",
            "ffd": "froxfield",
            "rdg": "reading",
            "pgt": "powergate",
        }
        return aliases.get(text, text)

    def _build_city_uid_map(self, topology: Dict[str, Any]) -> Dict[str, List[str]]:
        city_map: Dict[str, List[str]] = {}
        for element in topology.get("elements", []):
            if element.get("type") != "Transceiver":
                continue
            city = element.get("metadata", {}).get("location", {}).get("city")
            if not city:
                continue
            city_map.setdefault(self._normalize_city(city), []).append(str(element.get("uid")))
        return city_map

    def _select_endpoint_uid(self, city_map: Dict[str, List[str]], city: str, role: str) -> str:
        candidates = city_map.get(self._normalize_city(city), [])
        if not candidates:
            raise ValueError(f"No transceiver endpoint found in topology for city: {city}")
        if role == "source":
            return next((candidate for candidate in candidates if candidate.startswith("tx_")), candidates[0])
        return next((candidate for candidate in candidates if candidate.startswith("node_")), candidates[-1])

    def _select_transceiver_mode(self, sim_config: Dict[str, Any], equipment: Dict[str, Any]) -> Dict[str, Any]:
        requested_modulation = str(sim_config.get("modulation", "QPSK")).strip().upper()
        requested_trx_type = str(sim_config.get("transmitter_type", "")).strip().lower()
        requested_baud_rate = float(sim_config.get("baud_rate", 32.0)) * 1e9

        for trx in equipment.get("Transceiver", []):
            trx_type = str(trx.get("type_variety", "")).strip().lower()
            if requested_trx_type and trx_type != requested_trx_type:
                continue
            for mode in trx.get("mode", []):
                if str(mode.get("format", "")).strip().upper() != requested_modulation:
                    continue
                baud_rate = float(mode.get("baud_rate", requested_baud_rate))
                if abs(baud_rate - requested_baud_rate) > 1.0:
                    continue
                return {
                    "baud_rate_hz": baud_rate,
                    "bit_rate": float(mode.get("bit_rate", 100e9)),
                    "spacing_hz": float(mode.get("min_spacing", 50e9)),
                    "roll_off": float(mode.get("roll_off", 0.15)),
                    "tx_osnr_db": float(mode.get("tx_osnr", sim_config.get("tx_osnr_db", 40.0))),
                }
        raise ValueError(f"No transceiver mode matches {requested_trx_type} / {requested_modulation}.")

    def _default_si(self, equipment: Dict[str, Any]) -> Dict[str, Any]:
        si = equipment.get("SI", [])
        if isinstance(si, list):
            if not si:
                raise ValueError("Equipment file is missing the SI section.")
            return si[0]
        return si

    def _generate_config(self, scenario: Dict[str, Any], tool: str) -> Dict[str, Any]:
        _, equipment = self._load_topology_and_equipment()
        mode = self._select_transceiver_mode(
            {
                "modulation": scenario.get("modulation"),
                "transmitter_type": scenario.get("transmitter_type"),
                "baud_rate": scenario.get("baud_rate_gbaud", 32.0),
                "tx_osnr_db": scenario.get("tx_osnr_db", 40.0),
            },
            equipment,
        )
        si = self._default_si(equipment)
        # Use the transceiver mode spacing so different modulations can drive
        # distinct spectral occupancies in the GNPy spectrum payload.
        spacing_hz = float(mode["spacing_hz"])
        return {
            "scenario_id": scenario.get("scenario_id"),
            "network": scenario.get("network"),
            "source": scenario.get("source"),
            "destination": scenario.get("destination"),
            "path_nodes": scenario.get("path_nodes", []),
            "spans": scenario.get("spans", []),
            "launch_power": float(scenario.get("power", si.get("power_dbm", -5.5))),
            "channel_slots": int(scenario.get("channel_slots", 8)),
            "channel_pattern": str(scenario.get("channel_pattern", "")),
            "channels": int(scenario.get("channels", 0)),
            "modulation": str(scenario.get("modulation", "QPSK")).strip().upper(),
            "transmitter_type": str(scenario.get("transmitter_type", "")),
            "baud_rate": float(scenario.get("baud_rate_gbaud", 32.0)),
            "bit_rate_gbps": float(scenario.get("bit_rate_gbps", mode["bit_rate"] / 1e9)),
            "spacing_hz": spacing_hz,
            "roll_off": float(mode["roll_off"]),
            "tx_osnr_db": float(mode["tx_osnr_db"]),
            "f_min_hz": float(si.get("f_min", 194e12)),
            "tool": tool,
        }

    def _build_execution_error(self, engine: str, scenario_id: str, message: str, artifacts: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return {
            "engine": engine,
            "backend": "real",
            "status": "error",
            "metrics": {
                "error_message": message,
                "scenario_id": scenario_id,
            },
            "artifacts": artifacts or {},
        }

    def _build_no_signal_result(self, sim_config: Dict[str, Any], tool: str) -> Dict[str, Any]:
        return {
            "engine": tool,
            "backend": "derived",
            "status": "no_signal",
            "metrics": {
                "channels_simulated": 0,
                "channel_slots": int(sim_config.get("channel_slots", 0)),
                "launch_power_dbm": float(sim_config.get("launch_power", 0.0)),
                "transmitter_type": sim_config.get("transmitter_type"),
                "channel_pattern": sim_config.get("channel_pattern"),
                "note": "All channel slots are switched off in this pattern.",
            },
            "artifacts": {},
        }

    def _build_spectrum_payload(self, sim_config: Dict[str, Any]) -> Dict[str, Any]:
        pattern = str(sim_config.get("channel_pattern") or "").strip()
        slot_count = int(sim_config.get("channel_slots", len(pattern) or 1))
        pattern = pattern[:slot_count].ljust(slot_count, "0")
        spacing_hz = float(sim_config["spacing_hz"])
        f_min_hz = float(sim_config["f_min_hz"])
        spectrum = []
        for slot_index, state in enumerate(pattern):
            frequency = f_min_hz + slot_index * spacing_hz
            spectrum.append(
                {
                    "f_min": frequency,
                    "f_max": frequency,
                    "baud_rate": float(sim_config["baud_rate"]) * 1e9,
                    "slot_width": spacing_hz,
                    "delta_pdb": 0.0,
                    "roll_off": float(sim_config["roll_off"]),
                    "tx_osnr": float(sim_config["tx_osnr_db"]),
                    "tx_power_dbm": float(sim_config["launch_power"]) if state == "1" else -120.0,
                    "label": f"slot-{slot_index + 1}",
                }
            )
        return {"spectrum": spectrum}

    def _parse_show_channels(self, stdout_text: str) -> List[Dict[str, float]]:
        rows: List[Dict[str, float]] = []
        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped or not re.match(r"^\d+", stripped):
                continue
            parts = stripped.split()
            if len(parts) < 6:
                continue
            try:
                rows.append(
                    {
                        "channel_number": float(parts[0]),
                        "frequency_thz": float(parts[1]),
                        "channel_power_dbm": float(parts[2]),
                        "osnr_signal_bw_db": float(parts[3]),
                        "snr_nli_signal_bw_db": float(parts[4]),
                        "gsnr_signal_bw_db": float(parts[5]),
                    }
                )
            except ValueError:
                continue
        return rows

    def _parse_final_gsnr_01nm(self, stdout_text: str) -> float | None:
        match = re.search(r"Final GSNR \(0\.1 nm\):\s*[-\x1b\[\d;]*([0-9.+-]+)\s*dB", stdout_text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _materialize_gnpy_input_files(self, scenario_id: str) -> Tuple[Path, Path]:
        topology, equipment = self._load_topology_and_equipment()
        topology_path = self._runtime_dir / f"{scenario_id}_topology.json"
        equipment_path = self._runtime_dir / f"{scenario_id}_equipment.json"
        topology_path.write_text(json.dumps(topology, indent=2, ensure_ascii=False), encoding="utf-8")
        equipment_path.write_text(json.dumps(equipment, indent=2, ensure_ascii=False), encoding="utf-8")
        return topology_path, equipment_path

    def _parse_cli_output(
        self,
        stdout_text: str,
        stderr_text: str,
        sim_config: Dict[str, Any],
        command_artifact: Path,
        spectrum_artifact: Path,
        result_artifact: Path,
    ) -> Dict[str, Any]:
        channel_rows = self._parse_show_channels(stdout_text)
        pattern = str(sim_config.get("channel_pattern") or "").strip()
        active_indices = [index for index, state in enumerate(pattern) if state == "1"]
        if active_indices and len(channel_rows) >= max(active_indices) + 1:
            channel_rows = [channel_rows[index] for index in active_indices]
        elif active_indices:
            channel_rows = [row for row in channel_rows if row.get("channel_power_dbm", -999.0) > -80.0]
        gsnr_01nm = self._parse_final_gsnr_01nm(stdout_text)

        metrics: Dict[str, Any] = {
            "channels_simulated": len(channel_rows),
            "channel_slots": int(sim_config.get("channel_slots", 0)),
            "spans_simulated": len(sim_config.get("spans", [])),
            "launch_power_dbm": round(float(sim_config.get("launch_power", 0.0)), 3),
            "transmitter_type": sim_config.get("transmitter_type"),
            "channel_pattern": sim_config.get("channel_pattern"),
            "channel_details": channel_rows,
        }
        if channel_rows:
            mean_power = sum(item["channel_power_dbm"] for item in channel_rows) / len(channel_rows)
            mean_osnr = sum(item["osnr_signal_bw_db"] for item in channel_rows) / len(channel_rows)
            mean_gsnr = sum(item["gsnr_signal_bw_db"] for item in channel_rows) / len(channel_rows)
            mean_ase = sum(item["channel_power_dbm"] - item["osnr_signal_bw_db"] for item in channel_rows) / len(channel_rows)
            metrics.update(
                {
                    "rx_signal_power_dbm": round(mean_power, 3),
                    "osnr_signal_bw_db": round(mean_osnr, 3),
                    "gsnr_signal_bw_db": round(mean_gsnr, 3),
                    "ase_signal_bw_dbm": round(mean_ase, 3),
                }
            )
        if gsnr_01nm is not None:
            metrics["gsnr_0p1nm_db"] = round(gsnr_01nm, 3)

        parsed_result = {
            "engine": "gnpy",
            "backend": "real",
            "status": "ok" if channel_rows else "invalid_metrics",
            "metrics": metrics,
            "artifacts": {
                "gnpy_request_json": str(command_artifact),
                "gnpy_result_json": str(result_artifact),
                "gnpy_spectrum_json": str(spectrum_artifact),
            },
        }
        result_artifact.write_text(
            json.dumps(
                {
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "parsed_output": parsed_result,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return parsed_result

    def _run_gnpy_cli(self, sim_config: Dict[str, Any]) -> Dict[str, Any]:
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        scenario_id = str(sim_config.get("scenario_id") or "gnpy_cli")
        topology, _ = self._load_topology_and_equipment()
        topology_file, equipment_file = self._materialize_gnpy_input_files(scenario_id)
        city_map = self._build_city_uid_map(topology)
        source_uid = self._select_endpoint_uid(city_map, str(sim_config.get("source")), "source")
        destination_uid = self._select_endpoint_uid(city_map, str(sim_config.get("destination")), "destination")

        spectrum_payload = self._build_spectrum_payload(sim_config)
        spectrum_path = self._runtime_dir / f"{scenario_id}_spectrum.json"
        command_path = self._runtime_dir / f"{scenario_id}_request.json"
        result_path = self._runtime_dir / f"{scenario_id}_result.json"
        spectrum_path.write_text(json.dumps(spectrum_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        command = [
            sys.executable,
            str(self._root / "tools" / "cli_examples.py"),
            str(topology_file),
            source_uid,
            destination_uid,
            "-e",
            str(equipment_file),
            "--spectrum",
            str(spectrum_path),
            "--show-channels",
            "-po",
            str(sim_config["launch_power"]),
        ]
        command_path.write_text(
            json.dumps(
                {
                    "scenario_id": scenario_id,
                    "cli_command": command,
                    "source_uid": source_uid,
                    "destination_uid": destination_uid,
                    "topology": str(topology_file),
                    "equipment": str(equipment_file),
                    "launch_power_dbm": sim_config["launch_power"],
                    "channel_pattern": sim_config["channel_pattern"],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(command, cwd=str(self._root), capture_output=True, text=True, encoding="utf-8")
        self._log(f"GNPy CLI command written: {command_path}")
        self._log(f"GNPy spectrum written: {spectrum_path}")

        if completed.returncode != 0:
            result_path.write_text(
                json.dumps(
                    {"stdout": completed.stdout, "stderr": completed.stderr, "returncode": completed.returncode},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return self._build_execution_error(
                "gnpy",
                scenario_id,
                (completed.stderr or completed.stdout or f"GNPy CLI failed with exit code {completed.returncode}").strip(),
                artifacts={
                    "gnpy_request_json": str(command_path),
                    "gnpy_result_json": str(result_path),
                    "gnpy_spectrum_json": str(spectrum_path),
                },
            )

        return self._parse_cli_output(completed.stdout, completed.stderr, sim_config, command_path, spectrum_path, result_path)

    def _active_slot_indices(self, sim_config: Dict[str, Any]) -> List[int]:
        pattern = str(sim_config.get("channel_pattern") or "").strip()
        return [index for index, state in enumerate(pattern) if state == "1"]

    def _scenario_seed(self, scenario_id: str, salt: str = "") -> int:
        digest = sha256(f"{scenario_id}|{salt}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def _channel_frequency_offsets_hz(self, sim_config: Dict[str, Any]) -> List[float]:
        slot_indices = self._active_slot_indices(sim_config)
        if not slot_indices:
            return []
        slot_center = (int(sim_config.get("channel_slots", 1)) - 1) / 2.0
        spacing_hz = float(sim_config["spacing_hz"])
        return [(slot_index - slot_center) * spacing_hz for slot_index in slot_indices]

    def _estimate_osnr_db(self, launch_power_dbm: float, spans: List[Dict[str, Any]], tx_osnr_db: float, fc_hz: float) -> float | None:
        if not spans:
            return None

        planck_constant = 6.62607015e-34
        ref_bandwidth_hz = 12.5e9
        signal_power_watts = 10 ** ((float(launch_power_dbm) - 30.0) / 10.0)
        if signal_power_watts <= 0.0:
            return None

        noise_power_watts = signal_power_watts / max(10 ** (float(tx_osnr_db) / 10.0), 1e-12)
        for span in spans:
            loss_db = float(span.get("loss_coef_db_per_km", 0.2)) * float(span.get("span_length_km", 0.0))
            gain_linear = max(10 ** (loss_db / 10.0), 1.0)
            noise_figure_linear = 10 ** (float(span.get("noise_figure_db", 4.5)) / 10.0)
            noise_power_watts += noise_figure_linear * planck_constant * float(fc_hz) * max(gain_linear - 1.0, 0.0) * ref_bandwidth_hz

        if noise_power_watts <= 0.0:
            return None
        return 10.0 * math.log10(signal_power_watts / noise_power_watts)

    def _build_opticommpy_request_payload(
        self,
        sim_config: Dict[str, Any],
        tx_params: Dict[str, Any],
        spans: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "scenario_id": sim_config.get("scenario_id"),
            "engine": "opticommpy",
            "source": sim_config.get("source"),
            "destination": sim_config.get("destination"),
            "modulation": sim_config.get("modulation"),
            "transmitter_type": sim_config.get("transmitter_type"),
            "launch_power_dbm": sim_config.get("launch_power"),
            "slot_pattern": sim_config.get("channel_pattern"),
            "slot_count": sim_config.get("channel_slots"),
            "active_slot_indices": self._active_slot_indices(sim_config),
            "channel_frequency_offsets_hz": self._channel_frequency_offsets_hz(sim_config),
            "tx_params": tx_params,
            "spans": spans,
        }

    def _ensure_scalar(self, value: Any) -> float:
        import numpy as np

        value = np.real_if_close(value)
        if np.isscalar(value):
            return float(value)
        return float(np.asarray(value).reshape(-1)[0])

    def _to_2pol_matrix(self, value: Any) -> Any:
        import numpy as np

        arr = np.asarray(value)
        if arr.ndim == 1:
            return arr.reshape(-1, 1)
        if arr.ndim == 2:
            if arr.shape[1] in (1, 2):
                return arr
            if arr.shape[0] in (1, 2):
                return arr.T
            return arr

        arr = np.squeeze(arr)
        if arr.ndim == 1:
            return arr.reshape(-1, 1)
        if arr.ndim == 2:
            if arr.shape[1] in (1, 2):
                return arr
            if arr.shape[0] in (1, 2):
                return arr.T
        return arr.reshape(arr.shape[0], -1)

    def _normalize_per_pol(self, value: Any) -> Any:
        import numpy as np

        arr = self._to_2pol_matrix(value).astype(np.complex128)
        out = arr.copy()
        for index in range(out.shape[1]):
            power = np.mean(np.abs(out[:, index]) ** 2)
            if power > 0:
                out[:, index] /= np.sqrt(power)
        return out

    def _waveform_power_dbm(self, value: Any) -> float | None:
        import numpy as np

        arr = self._to_2pol_matrix(value)
        if arr.size == 0:
            return None
        power_watts = float(np.mean(np.abs(arr) ** 2))
        if power_watts <= 0:
            return None
        return 10.0 * math.log10(power_watts / 1e-3)

    def _align_lengths(self, left: Any, right: Any) -> Tuple[Any, Any]:
        import numpy as np

        left_arr = np.asarray(left)
        right_arr = np.asarray(right)
        length = min(len(left_arr), len(right_arr))
        return left_arr[:length], right_arr[:length]

    def _sample_with_offset(self, value: Any, samples_per_symbol: int, offset: int) -> Any:
        arr = self._to_2pol_matrix(value)
        if arr.shape[0] <= offset:
            return arr
        return arr[offset::samples_per_symbol, :]

    def _score_offset(self, rx_symbols: Any, tx_symbols: Any) -> float:
        import numpy as np

        rx = self._to_2pol_matrix(rx_symbols)
        tx = self._to_2pol_matrix(tx_symbols)
        if rx.shape[0] < 100:
            return float("-inf")

        rx_power = np.sum(np.abs(rx) ** 2, axis=1)
        tx_power = np.sum(np.abs(tx) ** 2, axis=1)
        length = min(len(rx_power), len(tx_power))
        rx_power = rx_power[:length] - np.mean(rx_power[:length])
        tx_power = tx_power[:length] - np.mean(tx_power[:length])
        denom = (np.linalg.norm(rx_power) * np.linalg.norm(tx_power)) + 1e-12
        return float(np.abs(np.vdot(rx_power, tx_power)) / denom)

    def _find_best_offset(self, matched_waveform: Any, tx_symbols: Any, samples_per_symbol: int) -> Tuple[int, float, Any]:
        best_offset = 0
        best_score = float("-inf")
        best_symbols = None
        for offset in range(samples_per_symbol):
            candidate = self._sample_with_offset(matched_waveform, samples_per_symbol, offset)
            score = self._score_offset(candidate, tx_symbols)
            if score > best_score:
                best_score = score
                best_offset = offset
                best_symbols = candidate
        if best_symbols is None:
            best_symbols = self._sample_with_offset(matched_waveform, samples_per_symbol, 0)
        return best_offset, best_score, best_symbols

    def _phase_align_to_reference(self, rx_symbols: Any, tx_symbols: Any) -> Any:
        import numpy as np

        rx = self._to_2pol_matrix(rx_symbols).astype(np.complex128)
        tx = self._to_2pol_matrix(tx_symbols).astype(np.complex128)
        length = min(rx.shape[0], tx.shape[0])
        rx = rx[:length, :].copy()
        tx = tx[:length, :]
        for index in range(min(rx.shape[1], tx.shape[1])):
            denom = np.vdot(rx[:, index], rx[:, index])
            if np.abs(denom) < 1e-20:
                continue
            rotation = np.vdot(tx[:, index], rx[:, index]) / denom
            if np.abs(rotation) > 0:
                rx[:, index] *= rotation / np.abs(rotation)
        return rx

    def _compute_polx_metrics(self, rx_symbols: Any, tx_symbols: Any, modulation_order: int) -> Dict[str, float]:
        from optic.comm.metrics import calcEVM, fastBERcalc, monteCarloGMI

        rx = self._to_2pol_matrix(rx_symbols)
        tx = self._to_2pol_matrix(tx_symbols)
        rx_pol, tx_pol = self._align_lengths(rx[:, 0], tx[:, 0])
        ber, _, snr = fastBERcalc(rx_pol, tx_pol, modulation_order, "qam")
        evm = calcEVM(rx_pol, modulation_order, "qam", symbTx=tx_pol)
        gmi, ngmi = monteCarloGMI(rx_pol, tx_pol, modulation_order, "qam")
        return {
            "ber": round(self._ensure_scalar(ber), 6),
            "evm": round(self._ensure_scalar(evm), 6),
            "gmi": round(self._ensure_scalar(gmi), 6),
            "ngmi": round(self._ensure_scalar(ngmi), 6),
            "snr_db": round(self._ensure_scalar(snr), 3),
        }

    def _save_constellation_manual(self, value: Any, filename: Path, title: str, max_points: int = 20000) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        arr = self._to_2pol_matrix(value)
        fig, axes = plt.subplots(1, min(2, arr.shape[1]), figsize=(6 * min(2, arr.shape[1]), 6), squeeze=False)
        labels = ["Pol-X", "Pol-Y"]
        for index in range(min(2, arr.shape[1])):
            axis = axes[0, index]
            points = np.asarray(arr[:, index]).reshape(-1)
            points = points[np.isfinite(np.real(points)) & np.isfinite(np.imag(points))]
            if len(points) > max_points:
                sample_index = np.linspace(0, len(points) - 1, max_points).astype(int)
                points = points[sample_index]
            if len(points) == 0:
                axis.text(0.5, 0.5, "No valid samples", ha="center", va="center")
            else:
                axis.scatter(np.real(points), np.imag(points), s=4, alpha=0.35)
            axis.set_title(labels[index])
            axis.set_xlabel("In-Phase")
            axis.set_ylabel("Quadrature")
            axis.grid(True)
            axis.axis("equal")
        fig.suptitle(title)
        fig.tight_layout()
        fig.savefig(filename, dpi=150, bbox_inches="tight")
        plt.close(fig)

    def _save_eye_manual(self, value: Any, samples_per_symbol: int, filename: Path, title: str, ntraces: int = 200, span_symbols: int = 2) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        arr = self._to_2pol_matrix(value)
        signal = np.real(np.asarray(arr[:, 0]).reshape(-1))
        signal = signal[np.isfinite(signal)]
        if len(signal) < samples_per_symbol * span_symbols * 2:
            plt.figure(figsize=(8, 4))
            plt.text(0.5, 0.5, "Not enough samples for eye diagram", ha="center", va="center")
            plt.title(title)
            plt.tight_layout()
            plt.savefig(filename, dpi=150, bbox_inches="tight")
            plt.close()
            return

        seg_len = samples_per_symbol * span_symbols
        n_possible = len(signal) // samples_per_symbol - span_symbols
        n_use = min(ntraces, max(1, n_possible))
        plt.figure(figsize=(8, 4))
        t_axis = list(range(seg_len))
        for index in range(n_use):
            start = index * samples_per_symbol
            segment = signal[start:start + seg_len]
            if len(segment) == seg_len:
                plt.plot(t_axis, segment, color="tab:blue", alpha=0.08, linewidth=0.8)
        plt.xlabel("Samples")
        plt.ylabel("Amplitude")
        plt.title(title)
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(filename, dpi=150, bbox_inches="tight")
        plt.close()

    def _save_processing_triptych(self, stage_map: Dict[str, Any], filename: Path, title: str, max_points: int = 12000) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        fig, axes = plt.subplots(1, 3, figsize=(16, 5), squeeze=False)
        for index, name in enumerate(list(stage_map.keys())[:3]):
            axis = axes[0, index]
            arr = self._to_2pol_matrix(stage_map[name])
            points = np.asarray(arr[:, 0]).reshape(-1)
            points = points[np.isfinite(np.real(points)) & np.isfinite(np.imag(points))]
            if len(points) > max_points:
                sample_index = np.linspace(0, len(points) - 1, max_points).astype(int)
                points = points[sample_index]
            axis.scatter(np.real(points), np.imag(points), s=4, alpha=0.35)
            axis.set_title(name)
            axis.set_xlabel("In-Phase")
            axis.set_ylabel("Quadrature")
            axis.grid(True)
            axis.axis("equal")
        fig.suptitle(title)
        fig.tight_layout()
        fig.savefig(filename, dpi=150, bbox_inches="tight")
        plt.close(fig)

    def _build_constellation_plot_data(self, value: Any, max_points: int = 20000) -> Dict[str, Any]:
        import numpy as np

        arr = self._to_2pol_matrix(value)
        labels = ["pol_x", "pol_y"]
        payload: Dict[str, Any] = {"polarizations": {}}
        for index in range(min(2, arr.shape[1])):
            points = np.asarray(arr[:, index]).reshape(-1)
            points = points[np.isfinite(np.real(points)) & np.isfinite(np.imag(points))]
            if len(points) > max_points:
                sample_index = np.linspace(0, len(points) - 1, max_points).astype(int)
                points = points[sample_index]
            payload["polarizations"][labels[index]] = {
                "i": [float(item) for item in np.real(points)],
                "q": [float(item) for item in np.imag(points)],
            }
        return payload

    def _build_eye_plot_data(
        self,
        value: Any,
        samples_per_symbol: int,
        ntraces: int = 200,
        span_symbols: int = 2,
    ) -> Dict[str, Any]:
        import numpy as np

        arr = self._to_2pol_matrix(value)
        signal = np.real(np.asarray(arr[:, 0]).reshape(-1))
        signal = signal[np.isfinite(signal)]
        seg_len = samples_per_symbol * span_symbols
        payload: Dict[str, Any] = {
            "samples_per_symbol": int(samples_per_symbol),
            "span_symbols": int(span_symbols),
            "segment_length": int(seg_len),
            "sample_axis": list(range(seg_len)),
            "traces": [],
        }
        if len(signal) < samples_per_symbol * span_symbols * 2:
            payload["warning"] = "Not enough samples for eye diagram"
            return payload

        n_possible = len(signal) // samples_per_symbol - span_symbols
        n_use = min(ntraces, max(1, n_possible))
        for index in range(n_use):
            start = index * samples_per_symbol
            segment = signal[start:start + seg_len]
            if len(segment) == seg_len:
                payload["traces"].append([float(item) for item in segment])
        return payload

    def _build_processing_triptych_data(self, stage_map: Dict[str, Any], max_points: int = 12000) -> Dict[str, Any]:
        import numpy as np

        payload: Dict[str, Any] = {"stages": {}}
        for name in list(stage_map.keys())[:3]:
            arr = self._to_2pol_matrix(stage_map[name])
            points = np.asarray(arr[:, 0]).reshape(-1)
            points = points[np.isfinite(np.real(points)) & np.isfinite(np.imag(points))]
            if len(points) > max_points:
                sample_index = np.linspace(0, len(points) - 1, max_points).astype(int)
                points = points[sample_index]
            payload["stages"][name] = {
                "i": [float(item) for item in np.real(points)],
                "q": [float(item) for item in np.imag(points)],
            }
        return payload

    def _build_psd_plot_data(
        self,
        waveform: Any,
        sampling_rate_hz: float,
        center_frequency_hz: float,
        max_points: int = 4096,
    ) -> Dict[str, Any]:
        import numpy as np

        arr = self._to_2pol_matrix(waveform)
        signal = np.asarray(arr[:, 0]).reshape(-1)
        if signal.size == 0:
            return {"frequency_hz": [], "psd_db": []}

        spectrum = np.fft.fftshift(np.fft.fft(signal))
        freqs = np.fft.fftshift(np.fft.fftfreq(signal.size, d=1.0 / sampling_rate_hz)) + center_frequency_hz
        psd = (np.abs(spectrum) ** 2) / max(signal.size, 1)
        psd_db = 10.0 * np.log10(np.maximum(psd, 1e-30))

        if signal.size > max_points:
            sample_index = np.linspace(0, signal.size - 1, max_points).astype(int)
            freqs = freqs[sample_index]
            psd_db = psd_db[sample_index]

        return {
            "frequency_hz": [float(item) for item in freqs],
            "psd_db": [float(item) for item in psd_db],
        }

    def _save_opticommpy_representative_artifacts(
        self,
        scenario_id: str,
        tx_waveform: Any,
        rx_waveform: Any,
        tx_symbols: Any,
        rx_frontend_symbols: Any,
        rx_after_edc_symbols: Any,
        rx_after_eq_symbols: Any,
        rx_after_cpr_symbols: Any | None,
        stage_triptych: Dict[str, Any] | None,
        final_metrics: Dict[str, Any],
        sampling_rate_hz: float,
        center_frequency_hz: float,
        best_offset: int,
        samples_per_symbol: int,
    ) -> Dict[str, str]:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from optic.plot import plotPSD

        artifacts: Dict[str, str] = {}
        plot_dir = self._optic_runtime_dir / f"{scenario_id}_plots"
        plot_dir.mkdir(parents=True, exist_ok=True)

        tx_psd_path = plot_dir / "tx_psd.png"
        rx_psd_path = plot_dir / "rx_psd.png"
        tx_ref_path = plot_dir / "tx_center_constellation_reference.png"
        frontend_path = plot_dir / "rx_frontend_constellation.png"
        edc_path = plot_dir / "rx_after_edc_constellation.png"
        eq_path = plot_dir / "rx_after_equalizer_constellation.png"
        cpr_path = plot_dir / "rx_after_cpr_constellation.png"
        eye_path = plot_dir / "rx_eye.png"
        metrics_path = plot_dir / "metrics.json"
        summary_path = plot_dir / "run_summary.json"
        tx_psd_data_path = plot_dir / "tx_psd_data.json"
        rx_psd_data_path = plot_dir / "rx_psd_data.json"
        tx_ref_data_path = plot_dir / "tx_center_constellation_reference_data.json"
        frontend_data_path = plot_dir / "rx_frontend_constellation_data.json"
        edc_data_path = plot_dir / "rx_after_edc_constellation_data.json"
        eq_data_path = plot_dir / "rx_after_equalizer_constellation_data.json"
        cpr_data_path = plot_dir / "rx_after_cpr_constellation_data.json"
        eye_data_path = plot_dir / "rx_eye_data.json"
        triptych_data_path = plot_dir / "rx_dsp_triptych_data.json"

        plt.figure()
        plotPSD(tx_waveform, Fs=sampling_rate_hz, Fc=center_frequency_hz)
        plt.savefig(tx_psd_path, dpi=150, bbox_inches="tight")
        plt.close()

        plt.figure()
        plotPSD(rx_waveform, Fs=sampling_rate_hz, Fc=center_frequency_hz)
        plt.savefig(rx_psd_path, dpi=150, bbox_inches="tight")
        plt.close()

        self._save_constellation_manual(tx_symbols, tx_ref_path, "TX Center-Channel Symbol Reference")
        self._save_constellation_manual(rx_frontend_symbols, frontend_path, "RX Frontend IQ Cloud (before EDC)")
        self._save_constellation_manual(rx_after_edc_symbols, edc_path, f"RX After EDC + MF (timed, best offset={best_offset})")
        self._save_constellation_manual(rx_after_eq_symbols, eq_path, "RX After MIMO Equalizer")
        if rx_after_cpr_symbols is not None:
            self._save_constellation_manual(rx_after_cpr_symbols, cpr_path, "RX After CPR")
        if stage_triptych:
            self._save_processing_triptych(stage_triptych, eye_path, "RX DSP Progression (Pol-X)")
        else:
            self._save_eye_manual(rx_after_eq_symbols, samples_per_symbol, eye_path, "RX Eye Diagram")

        tx_psd_data_path.write_text(
            json.dumps(self._build_psd_plot_data(tx_waveform, sampling_rate_hz, center_frequency_hz), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        rx_psd_data_path.write_text(
            json.dumps(self._build_psd_plot_data(rx_waveform, sampling_rate_hz, center_frequency_hz), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tx_ref_data_path.write_text(
            json.dumps(self._build_constellation_plot_data(tx_symbols), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        frontend_data_path.write_text(
            json.dumps(self._build_constellation_plot_data(rx_frontend_symbols), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        edc_data_path.write_text(
            json.dumps(self._build_constellation_plot_data(rx_after_edc_symbols), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        eq_data_path.write_text(
            json.dumps(self._build_constellation_plot_data(rx_after_eq_symbols), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if rx_after_cpr_symbols is not None:
            cpr_data_path.write_text(
                json.dumps(self._build_constellation_plot_data(rx_after_cpr_symbols), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        if stage_triptych:
            triptych_data_path.write_text(
                json.dumps(self._build_processing_triptych_data(stage_triptych), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            eye_data_path.write_text(
                json.dumps(self._build_eye_plot_data(rx_after_eq_symbols, samples_per_symbol), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        metrics_path.write_text(json.dumps(final_metrics, indent=2, ensure_ascii=False), encoding="utf-8")
        summary_path.write_text(
            json.dumps(
                {
                    "scenario_id": scenario_id,
                    "best_offset": int(best_offset),
                    "files": [
                        "tx_psd.png",
                        "rx_psd.png",
                        "tx_center_constellation_reference.png",
                        "rx_frontend_constellation.png",
                        "rx_after_edc_constellation.png",
                        "rx_after_equalizer_constellation.png",
                        "rx_eye.png",
                        "rx_after_cpr_constellation.png" if rx_after_cpr_symbols is not None else None,
                        "tx_psd_data.json",
                        "rx_psd_data.json",
                        "tx_center_constellation_reference_data.json",
                        "rx_frontend_constellation_data.json",
                        "rx_after_edc_constellation_data.json",
                        "rx_after_equalizer_constellation_data.json",
                        "rx_after_cpr_constellation_data.json" if rx_after_cpr_symbols is not None else None,
                        "rx_dsp_triptych_data.json" if stage_triptych else "rx_eye_data.json",
                        "metrics.json",
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        artifacts["tx_psd_png"] = str(tx_psd_path)
        artifacts["rx_psd_png"] = str(rx_psd_path)
        artifacts["tx_psd_data_json"] = str(tx_psd_data_path)
        artifacts["rx_psd_data_json"] = str(rx_psd_data_path)
        artifacts["tx_center_constellation_png"] = str(tx_ref_path)
        artifacts["tx_center_constellation_data_json"] = str(tx_ref_data_path)
        artifacts["eye_diagram_png"] = str(eye_path)
        artifacts["constellation_png"] = str(cpr_path if rx_after_cpr_symbols is not None else eq_path)
        artifacts["psd_png"] = str(rx_psd_path)
        artifacts["rx_frontend_constellation_png"] = str(frontend_path)
        artifacts["rx_frontend_constellation_data_json"] = str(frontend_data_path)
        artifacts["rx_after_edc_constellation_png"] = str(edc_path)
        artifacts["rx_after_edc_constellation_data_json"] = str(edc_data_path)
        artifacts["rx_after_equalizer_constellation_png"] = str(eq_path)
        artifacts["rx_after_equalizer_constellation_data_json"] = str(eq_data_path)
        if rx_after_cpr_symbols is not None:
            artifacts["rx_after_cpr_constellation_png"] = str(cpr_path)
            artifacts["rx_after_cpr_constellation_data_json"] = str(cpr_data_path)
        if stage_triptych:
            artifacts["dsp_triptych_data_json"] = str(triptych_data_path)
        else:
            artifacts["eye_diagram_data_json"] = str(eye_data_path)
        artifacts["metrics_json"] = str(metrics_path)
        artifacts["run_summary_json"] = str(summary_path)
        return artifacts

    def _channel_select_waveform(
        self,
        waveform: Any,
        sampling_rate_hz: float,
        channel_bandwidth_hz: float,
    ) -> Any:
        import numpy as np

        signal = np.asarray(waveform).reshape(-1)
        spectrum = np.fft.fft(signal)
        frequencies = np.fft.fftfreq(signal.size, d=1.0 / sampling_rate_hz)
        half_bandwidth = channel_bandwidth_hz / 2.0
        transition_band = max(channel_bandwidth_hz * 0.15, sampling_rate_hz / signal.size)

        window = np.zeros_like(frequencies, dtype=float)
        passband = np.abs(frequencies) <= half_bandwidth
        transition = (np.abs(frequencies) > half_bandwidth) & (np.abs(frequencies) <= half_bandwidth + transition_band)
        window[passband] = 1.0
        if np.any(transition):
            normalized = (np.abs(frequencies[transition]) - half_bandwidth) / transition_band
            window[transition] = 0.5 * (1.0 + np.cos(np.pi * normalized))

        return np.fft.ifft(spectrum * window)

    def _opticommpy_center_frequency_hz(self, sim_config: Dict[str, Any]) -> float:
        slot_count = int(sim_config.get("channel_slots", 1))
        spacing_hz = float(sim_config["spacing_hz"])
        f_min_hz = float(sim_config["f_min_hz"])
        return f_min_hz + spacing_hz * max(slot_count - 1, 0) / 2.0

    def _opticommpy_samples_per_symbol(self, modulation_order: int) -> int:
        if modulation_order <= 4:
            return 4
        if modulation_order <= 16:
            return 6
        return 8

    def _align_opticommpy_symbols(
        self,
        rx_waveform: Any,
        tx_symbols: Any,
        samples_per_symbol: int,
        filter_taps: int,
        modulation_order: int,
    ) -> Dict[str, Any]:
        import numpy as np
        from optic.comm.metrics import fastBERcalc

        waveform = np.asarray(rx_waveform).reshape(-1)
        tx_reference = np.asarray(tx_symbols).reshape(-1)
        max_lag = max(4 * samples_per_symbol, 8)
        best_candidate: Dict[str, Any] | None = None

        for sample_offset in range(max(samples_per_symbol, 1)):
            rx_symbols = waveform[sample_offset::max(samples_per_symbol, 1)]
            min_length = min(len(rx_symbols), len(tx_reference))
            if min_length <= 0:
                continue

            rx_symbols = np.asarray(rx_symbols[:min_length])
            tx_window = np.asarray(tx_reference[:min_length])
            guard_symbols = min(max(filter_taps // max(samples_per_symbol, 1), 8), max(min_length // 10, 0))
            if guard_symbols > 0 and min_length > 2 * guard_symbols:
                rx_symbols = rx_symbols[guard_symbols:-guard_symbols]
                tx_window = tx_window[guard_symbols:-guard_symbols]

            for lag in range(-max_lag, max_lag + 1):
                if lag < 0:
                    aligned_rx = rx_symbols[-lag:]
                    aligned_tx = tx_window[: len(aligned_rx)]
                elif lag > 0:
                    aligned_rx = rx_symbols[:-lag]
                    aligned_tx = tx_window[lag : lag + len(aligned_rx)]
                else:
                    aligned_rx = rx_symbols
                    aligned_tx = tx_window

                aligned_length = min(len(aligned_rx), len(aligned_tx))
                if aligned_length < 128:
                    continue

                aligned_rx = np.asarray(aligned_rx[:aligned_length])
                aligned_tx = np.asarray(aligned_tx[:aligned_length])
                phase_error = float(np.angle(np.vdot(aligned_rx, aligned_tx)))
                phase_aligned_rx = aligned_rx * np.exp(-1j * phase_error)
                ber, _, snr = fastBERcalc(phase_aligned_rx, aligned_tx, modulation_order, "qam")

                candidate = {
                    "sample_offset": int(sample_offset),
                    "symbol_lag": int(lag),
                    "phase_rotation_rad": phase_error,
                    "ber": float(ber[0]),
                    "snr_db": float(snr[0]),
                    "rx_symbols": phase_aligned_rx,
                    "tx_symbols": aligned_tx,
                }
                if best_candidate is None or candidate["ber"] < best_candidate["ber"] or (
                    math.isclose(candidate["ber"], best_candidate["ber"], abs_tol=1e-12)
                    and candidate["snr_db"] > best_candidate["snr_db"]
                ):
                    best_candidate = candidate

        if best_candidate is None:
            raise ValueError("Unable to align OptiCommPy symbols after propagation.")
        return best_candidate

    def _equalize_opticommpy_symbols(
        self,
        rx_symbols: Any,
        tx_symbols: Any,
        modulation_order: int,
    ) -> Dict[str, Any]:
        import numpy as np
        from optic.comm.metrics import fastBERcalc
        from optic.dsp.equalization import mimoAdaptEqualizer
        from optic.utils import parameters

        rx_input = np.asarray(rx_symbols).reshape(-1)
        tx_reference = np.asarray(tx_symbols).reshape(-1)
        train_length = min(max(len(rx_input) // 5, 256), max(len(rx_input) - 256, 0))
        if train_length < 128:
            ber, _, snr = fastBERcalc(rx_input, tx_reference, modulation_order, "qam")
            return {
                "rx_symbols": rx_input,
                "tx_symbols": tx_reference,
                "training_symbols": 0,
                "ber": float(ber[0]),
                "snr_db": float(snr[0]),
            }

        eval_length = len(rx_input) - train_length
        params = parameters()
        params.numIter = 1
        params.nTaps = 15
        params.mu = [5e-3, 0.0]
        params.SpS = 1
        params.alg = ["nlms", "static"]
        params.L = [train_length, eval_length]
        params.M = modulation_order
        params.constType = "qam"
        params.prgsBar = False

        equalized = mimoAdaptEqualizer(rx_input.reshape(-1, 1), params, tx_reference.reshape(-1, 1))
        equalized = np.asarray(equalized).reshape(-1)
        rx_eval = equalized[train_length : train_length + eval_length]
        tx_eval = tx_reference[train_length : train_length + eval_length]
        ber, _, snr = fastBERcalc(rx_eval, tx_eval, modulation_order, "qam")
        return {
            "rx_symbols": rx_eval,
            "tx_symbols": tx_eval,
            "training_symbols": int(train_length),
            "ber": float(ber[0]),
            "snr_db": float(snr[0]),
        }

    def _run_opticommpy(self, sim_config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import numpy as np
            from optic.comm.metrics import calcEVM, fastBERcalc, monteCarloGMI
            from optic.dsp.core import firFilter, pulseShape
            from optic.dsp.carrierRecovery import cpr, fourthPowerFOE
            from optic.dsp.equalization import edc, mimoAdaptEqualizer
            from optic.models.channels import manakovSSF
            from optic.models.devices import basicLaserModel, pdmCoherentReceiver
            from optic.models.tx import simpleWDMTx
            from optic.utils import parameters
        except Exception as exc:
            return self._build_execution_error(
                "opticommpy",
                str(sim_config.get("scenario_id")),
                f"Failed to import OptiCommPy modules: {exc}",
            )

        scenario_id = str(sim_config.get("scenario_id") or "opticommpy")
        self._optic_runtime_dir.mkdir(parents=True, exist_ok=True)
        request_path = self._optic_runtime_dir / f"{scenario_id}_request.json"
        result_path = self._optic_runtime_dir / f"{scenario_id}_result.json"

        active_slots = self._active_slot_indices(sim_config)
        frequency_offsets_hz = self._channel_frequency_offsets_hz(sim_config)
        if not active_slots:
            return self._build_no_signal_result(sim_config, "opticommpy")

        modulation = str(sim_config.get("modulation", "QPSK")).upper()
        modulation_order = {"QPSK": 4, "16QAM": 16, "64QAM": 64}.get(modulation)
        if modulation_order is None:
            return self._build_execution_error(
                "opticommpy",
                scenario_id,
                f"Unsupported modulation for OptiCommPy execution: {modulation}",
            )

        samples_per_symbol = 8
        filter_taps = 256
        bits_per_channel = 2**16
        baud_rate_hz = float(sim_config["baud_rate"]) * 1e9
        sampling_rate_hz = baud_rate_hz * samples_per_symbol
        roll_off = float(sim_config.get("roll_off", 0.15))
        spans = list(sim_config.get("spans", []))
        channel_slots = int(sim_config.get("channel_slots", len(str(sim_config.get("channel_pattern") or "")) or 1))
        pattern = str(sim_config.get("channel_pattern") or "").strip().ljust(channel_slots, "0")[:channel_slots]
        power_per_channel = [
            float(sim_config["launch_power"]) if state == "1" else -120.0
            for state in pattern
        ]

        tx_params = {
            "M": modulation_order,
            "constType": "qam",
            "Rs": baud_rate_hz,
            "SpS": samples_per_symbol,
            "nBits": bits_per_channel,
            "pulseType": "rrc",
            "nFilterTaps": filter_taps,
            "pulseRollOff": roll_off,
            "powerPerChannel": power_per_channel,
            "nChannels": channel_slots,
            "Fc": self._opticommpy_center_frequency_hz(sim_config),
            "laserLinewidth": 100e3,
            "wdmGridSpacing": float(sim_config["spacing_hz"]),
            "nPolModes": 2,
            "prgsBar": False,
        }

        request_path.write_text(
            json.dumps(
                self._build_opticommpy_request_payload(sim_config, tx_params, spans),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        try:
            tx = parameters()
            for key, value in tx_params.items():
                setattr(tx, key, value)
            tx.seed = self._scenario_seed(scenario_id, salt="wdm")

            waveform, symbols_tx_all, tx_runtime = simpleWDMTx(tx)
            waveform = np.asarray(waveform)
            if waveform.ndim == 1:
                waveform = waveform.reshape(-1, 1)
            tx_frequency_grid_hz = list(np.asarray(tx_runtime.wdmFreqGrid, dtype=float))

            propagated = waveform
            for span in spans:
                channel = parameters()
                channel.Ltotal = float(span.get("span_length_km", 0.0))
                channel.Lspan = float(span.get("span_length_km", 0.0))
                channel.hz = 0.1
                channel.alpha = float(span.get("loss_coef_db_per_km", 0.2))
                channel.D = float(span.get("dispersion_ps_per_nm_per_km", 16.7))
                channel.gamma = float(span.get("gamma_per_w_per_km", 1.27))
                channel.Fc = float(tx_params["Fc"])
                channel.Fs = sampling_rate_hz
                channel.amp = "edfa"
                channel.NF = float(span.get("noise_figure_db", 4.5))
                channel.G = float(span.get("edfa_gain_db", channel.alpha * channel.Lspan))
                channel.prgsBar = False
                propagated = manakovSSF(propagated, channel)

            rx_total_wdm_power_dbm = self._waveform_power_dbm(propagated)
            rx_per_channel_power_dbm = None
            if rx_total_wdm_power_dbm is not None and active_slots:
                rx_per_channel_power_dbm = rx_total_wdm_power_dbm - (10.0 * math.log10(len(active_slots)))

            pulse_params = parameters()
            pulse_params.pulseType = "rrc"
            pulse_params.SpS = samples_per_symbol
            pulse_params.N = filter_taps
            pulse_params.alpha = roll_off
            pulse_params.Ts = 1.0 / baud_rate_hz
            pulse = pulseShape(pulse_params)

            channel_metrics: List[Dict[str, Any]] = []
            plot_artifacts: Dict[str, str] = {}
            representative_slot = min(active_slots, key=lambda slot: abs(slot - ((channel_slots - 1) / 2.0)))
            representative_stage_payload: Dict[str, Any] | None = None
            total_distance = sum(float(span.get("span_length_km", 0.0)) for span in spans)

            for slot_index in active_slots:
                try:
                    tx_symbols = np.asarray(symbols_tx_all[:, :, slot_index])
                    frequency_offset_hz = float(tx_frequency_grid_hz[slot_index])
                    frontend_offset_hz = frequency_offset_hz + 64e6

                    lo_params = parameters()
                    lo_params.P = 10
                    lo_params.lw = 100e3
                    lo_params.RIN_var = 0
                    lo_params.Ns = len(propagated)
                    lo_params.Fs = sampling_rate_hz
                    lo_params.seed = self._scenario_seed(scenario_id, salt=f"lo-{slot_index}")
                    lo_params.freqShift = frontend_offset_hz
                    lo_signal = basicLaserModel(lo_params)
                    time_axis = np.arange(len(lo_signal)) / sampling_rate_hz
                    lo_signal = lo_signal * np.exp(1j * 2 * np.pi * lo_params.freqShift * time_axis)

                    pd_params = parameters()
                    pd_params.B = baud_rate_hz
                    pd_params.Fs = sampling_rate_hz
                    pd_params.ideal = True
                    pd_params.seed = self._scenario_seed(scenario_id, salt=f"pd-{slot_index}")

                    rx_frontend = pdmCoherentReceiver(propagated, lo_signal, np.pi / 3, pd_params)
                    frontend_symbols = self._normalize_per_pol(
                        self._sample_with_offset(rx_frontend, samples_per_symbol, samples_per_symbol // 2)
                    )

                    edc_params = parameters()
                    edc_params.L = total_distance
                    edc_params.D = float(spans[0].get("dispersion_ps_per_nm_per_km", 16.7)) if spans else 16.7
                    edc_params.Fc = float(tx_params["Fc"] + frequency_offset_hz)
                    edc_params.Fs = sampling_rate_hz
                    edc_params.Rs = baud_rate_hz

                    cd_compensated = edc(rx_frontend, edc_params)
                    matched = firFilter(pulse, cd_compensated)
                    best_offset, best_score, rx_mf_symbols = self._find_best_offset(matched, tx_symbols, samples_per_symbol)

                    tx_reference = self._normalize_per_pol(tx_symbols)
                    rx_after_edc = self._phase_align_to_reference(self._normalize_per_pol(rx_mf_symbols), tx_reference)

                    rx_foe, fo_est = fourthPowerFOE(self._normalize_per_pol(rx_mf_symbols), baud_rate_hz)
                    rx_foe = self._normalize_per_pol(rx_foe)

                    eq_params = parameters()
                    eq_params.nTaps = 9
                    eq_params.SpS = 1
                    eq_params.numIter = 5
                    eq_params.storeCoeff = False
                    eq_params.M = modulation_order
                    eq_params.constType = "qam"
                    eq_params.prgsBar = False
                    eq_params.returnResults = True
                    eq_params.alg = ["nlms", "nlms"]
                    eq_params.mu = [2e-3, 5e-4]
                    eq_params.L = [4000, max(1, len(tx_reference) - 4000)]

                    rx_eq_out, _, _, _ = mimoAdaptEqualizer(rx_foe, eq_params, tx_reference)
                    rx_eq_out = self._normalize_per_pol(rx_eq_out)
                    rx_after_eq = self._phase_align_to_reference(rx_eq_out, tx_reference)

                    cpr_out = None
                    try:
                        cpr_params = parameters()
                        cpr_params.alg = "bps"
                        cpr_params.N = 64
                        cpr_params.B = 64
                        cpr_params.M = modulation_order
                        cpr_params.constType = "qam"
                        cpr_result = cpr(rx_eq_out, param=cpr_params, symbTx=tx_reference)
                        cpr_out = cpr_result[0] if isinstance(cpr_result, tuple) else cpr_result
                        cpr_out = self._normalize_per_pol(cpr_out)
                    except Exception:
                        cpr_out = None

                    metric_rx = cpr_out if cpr_out is not None else rx_eq_out
                    metric_rx = self._phase_align_to_reference(metric_rx, tx_reference)
                    polx_metrics = self._compute_polx_metrics(metric_rx, tx_reference, modulation_order)

                    channel_metric = {
                        "slot_index": int(slot_index),
                        "frequency_offset_hz": float(frequency_offset_hz),
                        "rx_power_dbm": round(rx_per_channel_power_dbm, 3) if rx_per_channel_power_dbm is not None else None,
                        "ber": polx_metrics["ber"],
                        "evm": polx_metrics["evm"],
                        "gmi": polx_metrics["gmi"],
                        "ngmi": polx_metrics["ngmi"],
                        "snr_db": polx_metrics["snr_db"],
                        "sample_offset": int(best_offset),
                        "symbol_lag": 0,
                        "phase_rotation_rad": 0.0,
                        "equalizer_training_symbols": int(eq_params.L[0]),
                        "blind_timing_score": round(float(best_score), 6),
                        "estimated_frequency_offset_hz": round(float(fo_est), 3),
                    }
                    channel_metrics.append(channel_metric)

                    if slot_index == representative_slot:
                        representative_stage_payload = {
                            "channel_metric": channel_metric,
                            "tx_symbols": tx_reference,
                            "rx_frontend_symbols": frontend_symbols,
                            "rx_after_edc_symbols": rx_after_edc,
                            "rx_after_eq_symbols": rx_after_eq,
                            "rx_after_cpr_symbols": self._phase_align_to_reference(cpr_out, tx_reference) if cpr_out is not None else None,
                            "best_offset": best_offset,
                            "final_metrics": {
                                "final_metrics": polx_metrics,
                                "timing_recovery": {
                                    "best_offset": int(best_offset),
                                    "blind_score": float(best_score),
                                },
                                "frequency_recovery": {
                                    "configured_frequency_offset_hz": float(frontend_offset_hz),
                                    "estimated_frequency_offset_hz": float(fo_est),
                                },
                                "physical_settings": {
                                    "laser_linewidth_hz": 100e3,
                                    "equalizer_taps": int(eq_params.nTaps),
                                    "equalizer_num_iter": int(eq_params.numIter),
                                },
                            },
                            "stage_triptych": {
                                "After FOE": self._phase_align_to_reference(rx_foe, tx_reference),
                                "After EQ": rx_after_eq,
                                "After CPR": self._phase_align_to_reference(cpr_out, tx_reference) if cpr_out is not None else rx_after_eq,
                            },
                        }
                except Exception as exc:
                    channel_metrics.append(
                        {
                            "slot_index": int(slot_index),
                            "frequency_offset_hz": float(tx_frequency_grid_hz[slot_index]),
                            "rx_power_dbm": None,
                            "ber": None,
                            "evm": None,
                            "gmi": None,
                            "ngmi": None,
                            "snr_db": None,
                            "sample_offset": None,
                            "symbol_lag": None,
                            "phase_rotation_rad": None,
                            "equalizer_training_symbols": None,
                            "blind_timing_score": None,
                            "estimated_frequency_offset_hz": None,
                            "error_message": str(exc),
                        }
                    )

            osnr_db = self._estimate_osnr_db(
                launch_power_dbm=float(sim_config["launch_power"]),
                spans=spans,
                tx_osnr_db=float(sim_config.get("tx_osnr_db", 40.0)),
                fc_hz=float(sim_config["f_min_hz"]),
            )
            valid_channel_metrics = [item for item in channel_metrics if item.get("ber") is not None]
            representative_metric = representative_stage_payload["channel_metric"] if representative_stage_payload is not None else None
            if representative_metric is not None:
                result_status = "ok"
            elif valid_channel_metrics:
                result_status = "invalid_metrics"
            else:
                result_status = "error"

            if representative_stage_payload is not None:
                plot_artifacts = self._save_opticommpy_representative_artifacts(
                    scenario_id=scenario_id,
                    tx_waveform=np.asarray(waveform),
                    rx_waveform=np.asarray(propagated),
                    tx_symbols=representative_stage_payload["tx_symbols"],
                    rx_frontend_symbols=representative_stage_payload["rx_frontend_symbols"],
                    rx_after_edc_symbols=representative_stage_payload["rx_after_edc_symbols"],
                    rx_after_eq_symbols=representative_stage_payload["rx_after_eq_symbols"],
                    rx_after_cpr_symbols=representative_stage_payload["rx_after_cpr_symbols"],
                    stage_triptych=representative_stage_payload["stage_triptych"],
                    final_metrics=representative_stage_payload["final_metrics"],
                    sampling_rate_hz=sampling_rate_hz,
                    center_frequency_hz=float(tx_params["Fc"]),
                    best_offset=int(representative_stage_payload["best_offset"]),
                    samples_per_symbol=samples_per_symbol,
                )

            result = {
                "engine": "opticommpy",
                "backend": "real",
                "status": result_status,
                "metrics": {
                    "channels_simulated": len(valid_channel_metrics),
                    "channel_slots": int(sim_config.get("channel_slots", 0)),
                    "spans_simulated": len(spans),
                    "launch_power_dbm": round(float(sim_config.get("launch_power", 0.0)), 3),
                    "transmitter_type": sim_config.get("transmitter_type"),
                    "channel_pattern": sim_config.get("channel_pattern"),
                    "representative_slot_index": representative_metric.get("slot_index") if representative_metric is not None else None,
                    "representative_slot_valid": representative_metric is not None,
                    "rx_total_wdm_power_dbm": round(rx_total_wdm_power_dbm, 3) if rx_total_wdm_power_dbm is not None else None,
                    "rx_signal_power_dbm": representative_metric.get("rx_power_dbm") if representative_metric is not None else None,
                    "effective_snr_db": representative_metric.get("snr_db") if representative_metric is not None else None,
                    "osnr_signal_bw_db": round(osnr_db, 3) if osnr_db is not None else None,
                    "ber": representative_metric.get("ber") if representative_metric is not None else None,
                    "evm": representative_metric.get("evm") if representative_metric is not None else None,
                    "gmi": representative_metric.get("gmi") if representative_metric is not None else None,
                    "ngmi": representative_metric.get("ngmi") if representative_metric is not None else None,
                    "per_channel_metrics": channel_metrics,
                    "per_channel_received_power_dbm": [item["rx_power_dbm"] for item in valid_channel_metrics],
                    "warning_message": (
                        "Representative center-channel DSP failed; only non-representative per-channel metrics are available."
                        if representative_metric is None and valid_channel_metrics
                        else None
                    ),
                },
                "artifacts": {
                    "opticommpy_request_json": str(request_path),
                    "opticommpy_result_json": str(result_path),
                    **plot_artifacts,
                },
            }
            result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            return result
        except Exception as exc:
            result_path.write_text(
                json.dumps(
                    {"error_message": str(exc), "scenario_id": scenario_id},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return self._build_execution_error(
                "opticommpy",
                scenario_id,
                f"OptiCommPy execution failed: {exc}",
                artifacts={
                    "opticommpy_request_json": str(request_path),
                    "opticommpy_result_json": str(result_path),
                },
            )

    def execute(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        print()
        print(f"   > {scenario['scenario_id']} | {scenario['source']} -> {scenario['destination']}")
        tool = scenario["preferred_engine"]
        reason = "Scenario Expander selected this engine from the task semantics."
        print(f"      Engine: {tool} | Reason: {reason}")

        sim_config = self._generate_config(scenario, tool)
        print(
            "      Config: "
            f"power={sim_config['launch_power']} dBm, "
            f"trx={sim_config['transmitter_type']}, "
            f"modulation={sim_config['modulation']}, "
            f"baud={sim_config['baud_rate']} GBd, "
            f"slots={sim_config['channel_slots']}, "
            f"pattern={sim_config['channel_pattern']}"
        )

        if int(sim_config.get("channels", 0)) == 0:
            print("      No active channels in this pattern; recording a dataset row without simulation.")
            output = self._build_no_signal_result(sim_config, tool)
        elif tool == "gnpy":
            print("      Running GNPy through the CLI wrapper...")
            output = self._run_gnpy_cli(sim_config)
        elif tool == "opticommpy":
            print("      Running OptiCommPy waveform propagation and receiver DSP...")
            output = self._run_opticommpy(sim_config)
        else:
            output = self._build_execution_error(
                tool,
                str(scenario.get("scenario_id")),
                f"Execution engine is not implemented: {tool}",
            )

        metrics = output.get("metrics", {})
        self._log(
            json.dumps(
                {
                    "scenario_id": scenario.get("scenario_id"),
                    "engine": tool,
                    "sim_config": sim_config,
                    "output": output,
                },
                ensure_ascii=False,
            )
        )
        print(
            "      Result: "
            f"backend={output.get('backend')} | status={output.get('status')} | "
            f"SNR={metrics.get('effective_snr_db', metrics.get('gsnr_signal_bw_db'))} | "
            f"OSNR={metrics.get('osnr_signal_bw_db')} | "
            f"BER={metrics.get('ber', metrics.get('ber_mean'))}"
        )
        return {
            "scenario": scenario,
            "tool": tool,
            "reason": reason,
            "sim_config": sim_config,
            "output": output,
        }
