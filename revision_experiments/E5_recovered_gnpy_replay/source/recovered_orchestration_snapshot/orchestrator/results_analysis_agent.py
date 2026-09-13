from collections import Counter
import math
from typing import Any, Dict, List


class ResultsAnalysisAgent:
    def analyze(
        self,
        records: List[Dict[str, Any]],
        feasibility_profiles: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        _ = feasibility_profiles
        ranked_results = []
        best_per_path: Dict[str, Dict[str, Any]] = {}
        best_per_family: Dict[str, Dict[str, Any]] = {}
        engine_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        transmitter_counts: Counter[str] = Counter()
        modulation_counts: Counter[str] = Counter()
        unique_patterns = set()
        unique_powers = set()
        power_buckets: Dict[float, Dict[str, Any]] = {}

        for record in records:
            metrics = record["output"]["metrics"]
            scenario = record["scenario"]
            status = self._normalized_status(record["output"].get("status", "unknown"))
            score = self._coverage_score(record)
            item = {"record": record, "score": score}
            ranked_results.append(item)

            path_key = f"{scenario['source']}->{scenario['destination']}"
            if path_key not in best_per_path or score > best_per_path[path_key]["score"]:
                best_per_path[path_key] = item
            family_key = self._scenario_family_key(scenario)
            if family_key not in best_per_family or score > best_per_family[family_key]["score"]:
                best_per_family[family_key] = item

            engine_counts.update([record["tool"]])
            status_counts.update([status])
            transmitter_counts.update([str(scenario.get("transmitter_type", "unknown"))])
            modulation_counts.update([str(scenario.get("modulation", "unknown"))])
            unique_patterns.add(str(scenario.get("channel_pattern", "")))
            power = float(scenario.get("power", 0.0))
            unique_powers.add(power)

            bucket = power_buckets.setdefault(
                power,
                {"total": 0, "ok": 0, "gsnr_sum": 0.0, "gsnr_count": 0, "osnr_sum": 0.0, "osnr_count": 0},
            )
            bucket["total"] += 1
            if status == "ok":
                bucket["ok"] += 1

            gsnr_value = self._safe_float(metrics.get("gsnr_signal_bw_db", metrics.get("effective_snr_db")))
            if gsnr_value is not None:
                bucket["gsnr_sum"] += gsnr_value
                bucket["gsnr_count"] += 1

            osnr_value = self._safe_float(metrics.get("osnr_signal_bw_db", metrics.get("osnr_0p1nm_db")))
            if osnr_value is not None:
                bucket["osnr_sum"] += osnr_value
                bucket["osnr_count"] += 1

        ranked_results.sort(key=lambda item: item["score"], reverse=True)
        best = ranked_results[0] if ranked_results else None
        power_summary = {
            power: {
                "total": bucket["total"],
                "ok": bucket["ok"],
                "ok_ratio": round(bucket["ok"] / bucket["total"], 4) if bucket["total"] else 0.0,
                "mean_gsnr_db": round(bucket["gsnr_sum"] / bucket["gsnr_count"], 3) if bucket["gsnr_count"] else None,
                "mean_osnr_db": round(bucket["osnr_sum"] / bucket["osnr_count"], 3) if bucket["osnr_count"] else None,
            }
            for power, bucket in sorted(power_buckets.items())
        }

        return {
            "total_scenarios": len(records),
            "ok_scenarios": status_counts.get("ok", 0),
            "error_scenarios": status_counts.get("error", 0),
            "no_signal_scenarios": status_counts.get("no_signal", 0),
            "invalid_metric_scenarios": status_counts.get("invalid_metrics", 0),
            "best_result": best["record"] if best else None,
            "best_score": best["score"] if best else None,
            "ranked_results": ranked_results,
            "best_per_path": {key: value["record"] for key, value in best_per_path.items()},
            "best_per_family": {key: value["record"] for key, value in best_per_family.items()},
            "engine_counts": dict(engine_counts),
            "status_counts": dict(status_counts),
            "transmitter_counts": dict(transmitter_counts),
            "modulation_counts": dict(modulation_counts),
            "unique_channel_patterns": len([item for item in unique_patterns if item]),
            "unique_launch_powers_dbm": sorted(unique_powers),
            "power_summary": power_summary,
        }

    def _coverage_score(self, record: Dict[str, Any]) -> float:
        scenario = record["scenario"]
        output = record["output"]
        metrics = output["metrics"]
        status = self._normalized_status(output.get("status", "unknown"))

        gsnr = self._safe_float(metrics.get("gsnr_signal_bw_db"))
        osnr = self._safe_float(metrics.get("osnr_signal_bw_db"))

        score = 0.0
        if status == "ok":
            score += 100.0
        elif status == "invalid_metrics":
            score -= 25.0
        elif status == "no_signal":
            score -= 50.0
        else:
            score -= 100.0

        if gsnr is not None:
            score += gsnr * 2.0
        elif osnr is not None:
            score += osnr * 0.25

        if osnr is not None:
            score += osnr * 0.1

        score += min(int(scenario.get("channels", 0)), 8) * 0.25
        return score

    def _safe_float(self, value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return parsed

    def _normalized_status(self, value: Any) -> str:
        text = str(value or "unknown")
        if text == "not_applicable":
            return "no_signal"
        return text

    def _scenario_family_key(self, scenario: Dict[str, Any]) -> str:
        return "|".join(
            [
                str(scenario.get("destination", "")),
                str(scenario.get("transmitter_type", "")),
                str(scenario.get("modulation", "")),
                str(scenario.get("baud_rate_gbaud", "")),
                str(scenario.get("bit_rate_gbps", "")),
                str(scenario.get("channel_pattern", "")),
            ]
        )
