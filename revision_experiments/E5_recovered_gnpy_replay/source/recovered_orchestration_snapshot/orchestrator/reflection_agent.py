import json
from typing import Any, Dict

from utils.llm import call_llm, parse_llm_json


class ReflectionAgent:
    def _fallback_reflection(self, message: str) -> Dict[str, Any]:
        return {
            "action": "stop",
            "message": message,
            "recommendations": [],
            "planner_updates": {},
            "scenario_power_overrides": {},
            "feasibility_profiles": [],
        }

    def reflect(self, analysis: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        reflection_view = {
            "engine_counts": analysis.get("engine_counts", {}),
            "status_counts": analysis.get("status_counts", {}),
            "transmitter_counts": analysis.get("transmitter_counts", {}),
            "modulation_counts": analysis.get("modulation_counts", {}),
            "unique_channel_patterns": analysis.get("unique_channel_patterns", 0),
            "unique_launch_powers_dbm": analysis.get("unique_launch_powers_dbm", []),
            "power_summary": analysis.get("power_summary", {}),
            "best_per_path": {},
        }
        for path_key, record in analysis.get("best_per_path", {}).items():
            scenario = record["scenario"]
            reflection_view["best_per_path"][path_key] = {
                "distance_km": scenario.get("distance"),
                "transmitter_type": scenario.get("transmitter_type"),
                "modulation": scenario.get("modulation"),
                "baud_rate_gbaud": scenario.get("baud_rate_gbaud"),
                "launch_power_dbm": scenario.get("power"),
                "channels": scenario.get("channels"),
                "channel_pattern": scenario.get("channel_pattern"),
                "metrics": record["output"].get("metrics", {}),
            }
        prompt = f"""
You are the Reflection Agent in a multi-agent optical network data-generation system.

Review the dataset coverage and decide whether the search should stop or be refined.

Current task:
{json.dumps(task, indent=2)}

Rules:
- Focus on raw dataset coverage and the stability of the returned raw metrics for the selected engine.
- If dataset coverage across paths, transmitter types, or channel patterns is narrow, recommend refinement instead of stopping.
- For bulk QoT data generation, prefer broader coverage and more raw observations over a single "best" scenario.
- In raw GNPy workflows, treat GSNR, OSNR, and ASE as the primary KPIs and do not introduce BER or feasibility thresholds.
- In raw OptiCommPy workflows, treat BER, EVM, GMI, SNR, OSNR, and received power as observational outputs; do not demand feasibility-like BER targets just to stop dataset generation.
- Keep the modulation set conservative by default; do not ask to add 64QAM unless high-order modulation evaluation is explicitly requested.
- If the task already prefers "gnpy", keep the search focused on GNPy and treat any OptiCommPy usage as fallback-only behavior.
- For GNPy-focused network-level QoT studies, keep per-channel launch powers in a realistic planning window between -10 dBm and 0 dBm unless the user explicitly requests otherwise.
- If too many scenarios return invalid metrics or runtime errors, refine by shifting or narrowing the sweep inside that window instead of increasing powers above 0 dBm.
- If the scenario count is already very large, avoid adding extra sweep points; prefer replacing the sweep with a smaller, more realistic set.
- Use the per-power summary to identify the better launch-power region after the coarse sweep.
- If two neighboring power points look better, refine around them with 0.5 dB granularity inside the same window. For example, if -8 dBm and -6 dBm look best, propose a refined sweep such as [-8.0, -7.5, -7.0, -6.5, -6.0].
- Do not assume one global launch-power recommendation fits all scenario families.
- If the better power clearly depends on the transmitter, modulation, baud rate, destination, or channel pattern, prefer per-family refinements in "scenario_power_overrides" instead of one global "power_sweep_dbm".
- If coverage is broad and the raw KPIs look healthy, recommend stopping.
- If refinement is needed, include concrete planner-facing updates.
- Return only valid JSON.

Dataset summary:
{json.dumps(reflection_view, indent=2)}

Return JSON:
{{
  "action": "stop" or "refine",
  "message": "...",
  "recommendations": ["...", "..."],
  "planner_updates": {{
    "preferred_engine": "gnpy" or "opticommpy" or null,
    "modulations": ["QPSK", "16QAM"],
    "channels": [1, 10, 40],
    "kpis": ["GSNR", "OSNR", "ASE"],
    "power_sweep_dbm": [-10, -8, -6, -4, -2, 0] or [-8.0, -7.5, -7.0, -6.5, -6.0],
    "notes_append": "..."
  }},
  "scenario_power_overrides": {{
    "destination|transmitter_type|modulation|baud_rate_gbaud|bit_rate_gbps|channel_pattern": [-6.5, -6.0, -5.5]
  }}
}}
"""
        response = call_llm(prompt)
        result = parse_llm_json(response)
        if result is None:
            return self._fallback_reflection("Reflection returned null; stopping without refinement.")
        if not isinstance(result, dict):
            raise ValueError(f"Reflection agent returned a non-dict JSON payload: {result}")
        if "action" not in result or "message" not in result:
            raise ValueError(f"Reflection agent returned incomplete JSON: {result}")
        return self._normalize_reflection(result, analysis, task)

    def _normalize_reflection(
        self,
        reflection: Dict[str, Any],
        analysis: Dict[str, Any],
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized = dict(reflection)
        planner_updates_raw = normalized.get("planner_updates")
        planner_updates = dict(planner_updates_raw) if isinstance(planner_updates_raw, dict) else {}
        power_sweep = planner_updates.get("power_sweep_dbm")
        scenario_power_overrides_raw = normalized.get("scenario_power_overrides")
        scenario_power_overrides = (
            dict(scenario_power_overrides_raw)
            if isinstance(scenario_power_overrides_raw, dict)
            else {}
        )

        recommendations = normalized.get("recommendations")
        if not isinstance(recommendations, list):
            normalized["recommendations"] = []

        if task.get("preferred_engine") == "opticommpy":
            normalized["planner_updates"] = {}
            normalized["scenario_power_overrides"] = {}
            normalized["feasibility_profiles"] = []
            normalized = self._enforce_metric_sanity(normalized, analysis, task)
            return self._finalize_action(normalized, analysis, task)

        if task.get("preferred_engine") == "gnpy":
            derived_overrides = self._derive_family_power_overrides(analysis, task)
            if derived_overrides:
                scenario_power_overrides.update(derived_overrides)
                normalized["action"] = "refine"
            if not isinstance(power_sweep, list) or not power_sweep:
                derived = self._derive_refined_power_sweep(analysis, task)
                if derived:
                    planner_updates["power_sweep_dbm"] = derived
                    normalized["action"] = "refine"
            else:
                planner_updates["power_sweep_dbm"] = [float(value) for value in power_sweep]

            if scenario_power_overrides:
                planner_updates.setdefault(
                    "notes_append",
                    "Reflection added per-family launch-power refinement for the next iteration.",
                )

        normalized["planner_updates"] = planner_updates
        normalized["scenario_power_overrides"] = scenario_power_overrides
        normalized["feasibility_profiles"] = []
        normalized = self._enforce_metric_sanity(normalized, analysis, task)
        return self._finalize_action(normalized, analysis, task)

    def _enforce_metric_sanity(
        self,
        reflection: Dict[str, Any],
        analysis: Dict[str, Any],
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized = dict(reflection)
        planner_updates = dict(normalized.get("planner_updates", {}))
        status_counts = analysis.get("status_counts", {})
        total = max(1, int(analysis.get("total_scenarios", 0)))
        invalid_ratio = (
            int(status_counts.get("invalid_metrics", 0)) + int(status_counts.get("error", 0))
        ) / total

        best_metrics = (analysis.get("best_result") or {}).get("output", {}).get("metrics", {})
        metric_health = self._metric_health(best_metrics, task)

        unexpected_transmitters = []
        requested_inventory = task.get("transmitter_inventory")
        if isinstance(requested_inventory, dict) and requested_inventory:
            allowed = {str(key).strip().lower() for key in requested_inventory}
            observed = {
                str(key).strip().lower()
                for key in analysis.get("transmitter_counts", {})
            }
            unexpected_transmitters = sorted(observed - allowed)

        if unexpected_transmitters or invalid_ratio > 0.15 or not metric_health["healthy"]:
            normalized["action"] = "refine"
            reasons = []
            if unexpected_transmitters:
                reasons.append(f"unexpected transmitters observed: {unexpected_transmitters}")
            if invalid_ratio > 0.15:
                reasons.append(f"invalid/error ratio is high ({invalid_ratio:.1%})")
            if not metric_health["healthy"]:
                reasons.append(metric_health["reason"])
            normalized["message"] = (
                "Dataset coverage may be broad, but the raw QoT metrics are not trustworthy enough yet: "
                + "; ".join(reasons)
                + "."
            )
            recommendations = normalized.get("recommendations")
            if not isinstance(recommendations, list):
                recommendations = []
            if unexpected_transmitters:
                recommendations.append("Restrict scenario expansion to the explicitly requested transmitter inventory.")
            if not metric_health["healthy"]:
                recommendations.append(metric_health["recommendation"])
            normalized["recommendations"] = recommendations
            planner_updates.setdefault(
                "notes_append",
                "Reflection sanity check flagged missing or inconsistent raw metrics or scenario drift; refine before export.",
            )

        normalized["planner_updates"] = planner_updates
        return normalized

    def _safe_float(self, value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed != parsed:
            return None
        return parsed

    def _metric_health(self, best_metrics: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, str | bool]:
        preferred_engine = str(task.get("preferred_engine") or "").strip().lower()
        best_osnr = self._safe_float(best_metrics.get("osnr_signal_bw_db"))

        if preferred_engine == "opticommpy":
            best_snr = self._safe_float(best_metrics.get("effective_snr_db"))
            best_ber = self._safe_float(best_metrics.get("ber"))
            has_rx_power = self._safe_float(best_metrics.get("rx_signal_power_dbm")) is not None
            healthy = best_snr is not None and best_osnr is not None and best_ber is not None and has_rx_power
            return {
                "healthy": healthy,
                "reason": f"best OptiCommPy metrics are incomplete (SNR={best_snr}, OSNR={best_osnr}, BER={best_ber}, rx_power_present={has_rx_power})",
                "recommendation": "Do not classify the dataset as healthy while OptiCommPy scenarios are missing SNR, OSNR, BER, or received-power outputs.",
            }

        best_gsnr = self._safe_float(best_metrics.get("gsnr_signal_bw_db"))
        healthy = not (
            best_gsnr is None
            or best_gsnr < 7.0
            or (best_osnr is not None and best_gsnr < 2.0 and best_osnr - best_gsnr > 15.0)
        )
        return {
            "healthy": healthy,
            "reason": f"best GSNR is not healthy (GSNR={best_gsnr}, OSNR={best_osnr})",
            "recommendation": "Do not classify the dataset as healthy while GSNR remains implausibly low or inconsistent with OSNR.",
        }

    def _derive_refined_power_sweep(self, analysis: Dict[str, Any], task: Dict[str, Any]) -> list[float] | None:
        power_summary = analysis.get("power_summary", {})
        if not power_summary:
            return None

        coarse_sweep = self._observed_power_sweep(analysis)
        if any(abs(round(value * 2) / 2 - value) > 1e-9 for value in coarse_sweep):
            return None
        if len(coarse_sweep) > 5:
            return None

        ranked = sorted(
            (
                (
                    float(power),
                    float(summary.get("ok_ratio", 0.0)),
                    float(summary.get("mean_gsnr_db", -1e9) if summary.get("mean_gsnr_db") is not None else -1e9),
                )
                for power, summary in power_summary.items()
            ),
            key=lambda item: (item[1], item[2]),
            reverse=True,
        )
        if len(ranked) < 2:
            return None

        best_power = ranked[0][0]
        neighbor = None
        for candidate_power, _, _ in ranked[1:]:
            if abs(candidate_power - best_power) <= 2.1:
                neighbor = candidate_power
                break
        if neighbor is None:
            return None

        low = max(-10.0, min(best_power, neighbor))
        high = min(0.0, max(best_power, neighbor))
        refined = []
        current = low
        while current <= high + 1e-9:
            refined.append(round(current, 1))
            current += 0.5
        if len(refined) >= 2 and refined != coarse_sweep:
            return refined
        return None

    def _derive_family_power_overrides(self, analysis: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, list[float]]:
        coarse_sweep = self._observed_power_sweep(analysis)
        if not coarse_sweep:
            return {}

        overrides: Dict[str, list[float]] = {}
        for family_key, record in analysis.get("best_per_family", {}).items():
            scenario = record.get("scenario", {})
            try:
                best_power = float(scenario.get("power"))
            except (TypeError, ValueError):
                continue

            if best_power <= min(coarse_sweep):
                refined = [best_power, best_power + 0.5, best_power + 1.0]
            elif best_power >= max(coarse_sweep):
                refined = [best_power - 1.0, best_power - 0.5, best_power]
            else:
                refined = [best_power - 0.5, best_power, best_power + 0.5]

            bounded = sorted(
                {
                    round(power, 1)
                    for power in refined
                    if -10.0 <= power <= 0.0
                }
            )
            if bounded and bounded != coarse_sweep:
                overrides[family_key] = bounded
        return overrides

    def _observed_power_sweep(self, analysis: Dict[str, Any]) -> list[float]:
        observed = analysis.get("unique_launch_powers_dbm", [])
        if not isinstance(observed, list):
            return []

        normalized: list[float] = []
        for value in observed:
            try:
                normalized.append(float(value))
            except (TypeError, ValueError):
                continue
        return sorted(set(normalized))

    def _finalize_action(self, reflection: Dict[str, Any], analysis: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(reflection)
        planner_updates = dict(normalized.get("planner_updates", {}))
        scenario_power_overrides = {
            str(key): value
            for key, value in dict(normalized.get("scenario_power_overrides", {})).items()
            if isinstance(value, list) and value
        }
        observed_powers = self._observed_power_sweep(analysis)
        status_counts = analysis.get("status_counts", {})
        total = max(1, int(analysis.get("total_scenarios", 0)))
        invalid_ratio = (
            int(status_counts.get("invalid_metrics", 0)) + int(status_counts.get("error", 0))
        ) / total
        best_metrics = (analysis.get("best_result") or {}).get("output", {}).get("metrics", {})
        metric_health = self._metric_health(best_metrics, task)
        broad_power_coverage = len(observed_powers) >= 5
        broad_pattern_coverage = int(analysis.get("unique_channel_patterns", 0)) >= 8

        if str(task.get("preferred_engine") or "").strip().lower() == "opticommpy":
            if metric_health["healthy"] and invalid_ratio <= 0.15:
                normalized["action"] = "stop"
                normalized["planner_updates"] = {}
                normalized["scenario_power_overrides"] = {}
                return normalized
            normalized["action"] = "refine"
            normalized["planner_updates"] = {}
            normalized["scenario_power_overrides"] = {}
            return normalized

        requested_power_sweep = planner_updates.get("power_sweep_dbm")
        if isinstance(requested_power_sweep, list) and requested_power_sweep:
            requested_power_sweep = self._normalize_power_list(requested_power_sweep)
            if requested_power_sweep == observed_powers:
                planner_updates.pop("power_sweep_dbm", None)
        else:
            planner_updates.pop("power_sweep_dbm", None)

        filtered_overrides: Dict[str, list[float]] = {}
        for family_key, values in scenario_power_overrides.items():
            normalized_values = self._normalize_power_list(values)
            if normalized_values and normalized_values != observed_powers:
                filtered_overrides[family_key] = normalized_values

        # After a healthy coarse sweep with broad dataset coverage, treat
        # auto-generated per-family refinements as optional hints rather than
        # mandatory next-step work.
        if broad_power_coverage and broad_pattern_coverage and invalid_ratio <= 0.15 and metric_health["healthy"]:
            filtered_overrides = {}

        has_real_refinement = bool(planner_updates.get("power_sweep_dbm")) or bool(filtered_overrides)
        if not metric_health["healthy"] or invalid_ratio > 0.15:
            normalized["action"] = "refine"
        else:
            normalized["action"] = "refine" if has_real_refinement else "stop"

        if normalized["action"] == "stop":
            planner_updates = {
                key: value
                for key, value in planner_updates.items()
                if key not in {"power_sweep_dbm", "notes_append"}
            }
            filtered_overrides = {}

        normalized["planner_updates"] = planner_updates
        normalized["scenario_power_overrides"] = filtered_overrides
        return normalized

    def _normalize_power_list(self, values: list[Any]) -> list[float]:
        normalized = []
        for value in values:
            try:
                normalized.append(round(float(value), 1))
            except (TypeError, ValueError):
                continue
        return sorted(set(normalized))
