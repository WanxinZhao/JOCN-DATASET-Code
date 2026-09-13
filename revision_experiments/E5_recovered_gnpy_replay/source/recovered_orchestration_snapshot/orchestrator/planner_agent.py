import json
import re
from typing import Any, Dict

from utils.llm import call_llm, parse_llm_json


class PlannerAgent:
    def _requires_detailed_physical_engine(self, user_input: str) -> bool:
        user_text = user_input.lower()
        return any(
            token in user_text
            for token in [
                "waveform",
                "ber",
                "evm",
                "gmi",
                "dsp",
                "opticommpy",
                "physical-layer",
                "coherent transmission",
                "receiver dsp",
            ]
        )

    def _extract_requested_voyager_count(self, user_input: str) -> int | None:
        patterns = [
            r"\buse\s+(\d+)\s+voyager\s+transmitters?\b",
            r"\busing\s+(\d+)\s+voyager\s+transmitters?\b",
            r"\b(\d+)\s+voyager\s+transmitters?\s+only\b",
            r"\buse\s+(\d+)\s+voyagers?\b",
            r"\busing\s+(\d+)\s+voyagers?\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, user_input, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def _wants_all_channels_active(self, user_input: str) -> bool:
        user_text = user_input.lower()
        return (
            "binary" not in user_text
            and "on/off" not in user_text
            and "enumerate" not in user_text
            and "all binary" not in user_text
            and ("physical-layer transmission dataset" in user_text or "coherent transmission" in user_text)
        )

    def _infer_fixed_channel_pattern(self, user_input: str, voyager_count: int | None) -> str | None:
        if voyager_count and self._wants_all_channels_active(user_input):
            return "1" * voyager_count
        return None

    def _reflection_prompt_view(self, reflection: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not reflection:
            return None
        return {
            "action": reflection.get("action"),
            "message": reflection.get("message"),
            "recommendations": reflection.get("recommendations", [])[:2],
            "planner_updates": reflection.get("planner_updates", {}),
        }

    def _fallback_plan(
        self,
        user_input: str,
        prior_task: Dict[str, Any] | None = None,
        reflection: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        user_text = user_input.lower()
        wants_waveform = self._requires_detailed_physical_engine(user_input)
        voyager_count = self._extract_requested_voyager_count(user_input)
        fixed_channel_pattern = self._infer_fixed_channel_pattern(user_input, voyager_count)
        planner_updates = (reflection or {}).get("planner_updates", {})
        task = dict(prior_task or {})
        task.update(
            {
                "network": task.get("network", "NDFF"),
                "source": task.get("source", "Bristol"),
                "destination": None,
                "scope": "all_paths_from_source" if "all" in user_text or "starting from" in user_text else "single_path",
                "task": "qot_estimation",
                "task_mode": "dataset_generation",
                "simulation_fidelity": "detailed_physical" if wants_waveform else "coarse_network",
                "preferred_engine": "opticommpy" if wants_waveform else ("gnpy" if any(token in user_text for token in ["gnpy", "qot", "path", "dataset"]) else None),
                "modulations": ["QPSK", "16QAM"],
                "kpis": ["BER", "EVM", "GMI", "SNR", "OSNR", "RX_POWER"] if wants_waveform else ["GSNR", "OSNR", "ASE"],
                "transmitter_inventory": {"voyager": voyager_count or 8} if "voyager" in user_text else {"voyager": 8, "teraflex": 4},
                "channel_slots": voyager_count or 8,
                "channel_activation_mode": "binary_enumeration" if any(token in user_text for token in ["2^8", "2**8", "binary", "on/off"]) else ("fixed_pattern" if fixed_channel_pattern else "representative"),
                "max_channel_patterns": 256 if any(token in user_text for token in ["2^8", "2**8", "binary", "on/off"]) else 16,
                "fixed_channel_pattern": fixed_channel_pattern,
                "power_sweep_dbm": None,
                "max_dataset_points": 50000,
                "notes": "Fallback planner output based on the user request.",
            }
        )
        if isinstance(planner_updates, dict):
            task.update({key: value for key, value in planner_updates.items() if value is not None})
        return task

    def _normalize_task(self, task: Dict[str, Any], user_input: str) -> Dict[str, Any]:
        normalized = dict(task)
        user_text = user_input.lower()

        if normalized.get("preferred_engine") not in {"gnpy", "opticommpy", None}:
            normalized["preferred_engine"] = None

        if self._requires_detailed_physical_engine(user_input):
            normalized["preferred_engine"] = "opticommpy"
            normalized["simulation_fidelity"] = "detailed_physical"
            normalized["task_mode"] = normalized.get("task_mode", "dataset_generation")
            normalized["kpis"] = ["BER", "EVM", "GMI", "SNR", "OSNR", "received_power"]
            notes = str(normalized.get("notes", "")).strip()
            if notes:
                notes = notes.replace("GNPy", "OptiCommPy")
                notes = notes.replace("QoT", "physical-layer transmission")
            normalized["notes"] = notes or "OptiCommPy physical-layer transmission dataset generation request."

        if not isinstance(normalized.get("modulations"), list) or not normalized["modulations"]:
            normalized["modulations"] = ["QPSK", "16QAM"]
        normalized["modulations"] = [
            str(item).strip().upper()
            for item in normalized["modulations"]
            if str(item).strip().upper() in {"QPSK", "16QAM", "64QAM"}
        ] or ["QPSK", "16QAM"]

        if not isinstance(normalized.get("kpis"), list) or not normalized["kpis"]:
            normalized["kpis"] = ["GSNR", "OSNR", "ASE"]

        if not isinstance(normalized.get("transmitter_inventory"), dict) or not normalized["transmitter_inventory"]:
            normalized["transmitter_inventory"] = {"voyager": 8, "teraflex": 4}

        requested_voyager_count = self._extract_requested_voyager_count(user_input)
        if requested_voyager_count and "voyager" in normalized["transmitter_inventory"]:
            normalized["transmitter_inventory"]["voyager"] = requested_voyager_count

        try:
            normalized["channel_slots"] = max(1, int(normalized.get("channel_slots", 8)))
        except (TypeError, ValueError):
            normalized["channel_slots"] = 8
        if requested_voyager_count:
            normalized["channel_slots"] = requested_voyager_count

        activation_mode = str(normalized.get("channel_activation_mode", "representative")).strip().lower()
        if "2^8" in user_text or "2**8" in user_text or "on/off" in user_text:
            activation_mode = "binary_enumeration"
        fixed_channel_pattern = normalized.get("fixed_channel_pattern")
        if not isinstance(fixed_channel_pattern, str) or not fixed_channel_pattern.strip():
            fixed_channel_pattern = self._infer_fixed_channel_pattern(user_input, requested_voyager_count)
        if fixed_channel_pattern:
            activation_mode = "fixed_pattern"
        normalized["channel_activation_mode"] = activation_mode if activation_mode in {"representative", "binary_enumeration", "fixed_pattern"} else "representative"
        normalized["fixed_channel_pattern"] = fixed_channel_pattern

        max_patterns = normalized.get("max_channel_patterns", 256 if normalized["channel_activation_mode"] == "binary_enumeration" else 16)
        try:
            normalized["max_channel_patterns"] = max(1, int(max_patterns))
        except (TypeError, ValueError):
            normalized["max_channel_patterns"] = 256 if normalized["channel_activation_mode"] == "binary_enumeration" else 16

        max_dataset_points = normalized.get("max_dataset_points", 50000)
        try:
            normalized["max_dataset_points"] = max(1, int(max_dataset_points))
        except (TypeError, ValueError):
            normalized["max_dataset_points"] = 50000

        power_sweep = normalized.get("power_sweep_dbm")
        normalized["power_sweep_dbm"] = [float(value) for value in power_sweep] if isinstance(power_sweep, list) and power_sweep else None
        normalized.setdefault("notes", "")
        return normalized

    def plan(
        self,
        user_input: str,
        reflection: Dict[str, Any] | None = None,
        prior_task: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        reflection_view = self._reflection_prompt_view(reflection)
        reflection_block = json.dumps(reflection_view, indent=2) if reflection_view else "null"
        prior_task_block = json.dumps(prior_task, indent=2) if prior_task else "null"
        prompt = f"""
You are the Planner Agent in a multi-agent optical network digital twin system.

Convert the user request into a structured task for downstream agents.

The system supports:
- GNPy for network-level QoT/path dataset generation.
- OptiCommPy for channel-level waveform / BER / DSP physical simulation.

Planning rules:
- Choose the engine from the task semantics, not from hardcoded forcing.
- For all-path QoT datasets, link/path QoT estimation, or planning-oriented raw datasets, choose "gnpy".
- Choose "opticommpy" only when the user explicitly asks for waveform, BER, DSP, nonlinear physical-channel analysis, or OptiCommPy.
- Respect the physical-layer database and testbed files already present in the workspace.
- If the user asks for binary on/off enumeration across N slots, keep that request exactly.
- If the user asks to use exactly N transmitters and does not ask for enumeration, set both the transmitter inventory and channel slots to N.
- For raw physical-layer coherent-transmission datasets with exactly N transmitters, prefer a fixed all-on pattern of N active channels unless the user explicitly requests pattern enumeration.
- Do not force 1/10/40 channel buckets when the user explicitly asked for another slot count.
- Use only supported modulation labels: "QPSK", "16QAM", "64QAM".
- For raw GNPy datasets, prefer KPIs ["GSNR", "OSNR", "ASE"] unless the user explicitly asks for more.
- If the user does not ask for a power sweep, keep "power_sweep_dbm" as null and let execution start from the equipment default.
- If reflection suggests updates, incorporate them.
- Return only JSON.

User request:
{user_input}

Previous task, if any:
{prior_task_block}

Reflection feedback from the last iteration, if any:
{reflection_block}

Return JSON:
{{
  "network": "...",
  "source": "...",
  "destination": "..." or null,
  "scope": "single_path" or "all_paths_from_source",
  "task": "qot_estimation",
  "task_mode": "dataset_generation" or "targeted_analysis",
  "simulation_fidelity": "coarse_network" or "detailed_physical",
  "preferred_engine": "gnpy" or "opticommpy" or null,
  "modulations": ["QPSK", "16QAM"],
  "kpis": ["GSNR", "OSNR", "ASE"],
  "transmitter_inventory": {{"voyager": 8}},
  "channel_slots": 8,
  "channel_activation_mode": "representative" or "binary_enumeration" or "fixed_pattern",
  "fixed_channel_pattern": "111" or null,
  "max_channel_patterns": 256,
  "max_dataset_points": 50000,
  "power_sweep_dbm": null,
  "notes": "..."
}}
"""
        try:
            response = call_llm(prompt)
            result = self._normalize_task(parse_llm_json(response), user_input)
        except Exception:
            result = self._normalize_task(self._fallback_plan(user_input, prior_task=prior_task, reflection=reflection), user_input)

        if "network" not in result or "source" not in result or "scope" not in result:
            raise ValueError(f"Planner returned incomplete task JSON: {result}")
        return result
