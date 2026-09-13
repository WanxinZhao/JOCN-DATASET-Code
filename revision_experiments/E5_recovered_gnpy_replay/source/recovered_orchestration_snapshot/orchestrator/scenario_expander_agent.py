import json
import random
from itertools import islice, product
from pathlib import Path
from typing import Any, Dict, List

from orchestrator.physical_layer_store import PhysicalLayerStore
from utils.llm import call_llm, parse_llm_json


class ScenarioExpanderAgent:
    def __init__(self, equipment_path: str = "eqpt_config_NDFF.json") -> None:
        self._equipment_path = Path(equipment_path)
        self._store = PhysicalLayerStore(equipment_path=equipment_path)
        self._equipment_cache: Dict[str, Any] | None = None
        self._transceiver_modes_cache: Dict[str, List[Dict[str, Any]]] | None = None

    def _load_equipment(self) -> Dict[str, Any]:
        if self._equipment_cache is None:
            _, self._equipment_cache = self._store.load_topology_and_equipment()
        return self._equipment_cache

    def _default_launch_power(self) -> float:
        equipment = self._load_equipment()
        si = equipment.get("SI", [])
        if isinstance(si, list) and si:
            return float(si[0].get("power_dbm", -5.5))
        if isinstance(si, dict):
            return float(si.get("power_dbm", -5.5))
        return -5.5

    def _load_transceiver_modes(self) -> Dict[str, List[Dict[str, Any]]]:
        if self._transceiver_modes_cache is not None:
            return self._transceiver_modes_cache

        equipment = self._load_equipment()
        supported: Dict[str, List[Dict[str, Any]]] = {}
        for trx in equipment.get("Transceiver", []):
            trx_type = str(trx.get("type_variety", "")).strip().lower()
            if not trx_type:
                continue
            modes: List[Dict[str, Any]] = []
            for mode in trx.get("mode", []):
                modulation = str(mode.get("format", "")).strip().upper()
                if modulation not in {"QPSK", "16QAM", "64QAM"}:
                    continue
                modes.append(
                    {
                        "transmitter_type": trx_type,
                        "modulation": modulation,
                        "baud_rate_gbaud": float(mode.get("baud_rate", 32e9)) / 1e9,
                        "bit_rate_gbps": float(mode.get("bit_rate", 100e9)) / 1e9,
                        "min_spacing_ghz": float(mode.get("min_spacing", 50e9)) / 1e9,
                        "tx_osnr_db": float(mode.get("tx_osnr", 40.0)),
                    }
                )
            if modes:
                supported[trx_type] = modes

        self._transceiver_modes_cache = supported
        return supported

    def _default_inventory(self) -> Dict[str, int]:
        return {"voyager": 8, "teraflex": 4}

    def _scenario_family_key(self, destination: str, mode: Dict[str, Any], channel_pattern: str) -> str:
        return "|".join(
            [
                destination,
                str(mode.get("transmitter_type", "")),
                str(mode.get("modulation", "")),
                str(mode.get("baud_rate_gbaud", "")),
                str(mode.get("bit_rate_gbps", "")),
                channel_pattern,
            ]
        )

    def _binary_patterns(
        self,
        slot_count: int,
        max_patterns: int,
        randomized: bool = False,
        rng: random.Random | None = None,
    ):
        slot_count = max(1, slot_count)
        max_patterns = max(1, max_patterns)
        total_patterns = 1 << slot_count
        target = min(max_patterns, total_patterns)

        if not randomized:
            for bits in islice(product("01", repeat=slot_count), target):
                yield "".join(bits)
            return

        rng = rng or random.Random()
        seen: set[int] = set()
        while len(seen) < target:
            value = rng.randrange(total_patterns)
            if value in seen:
                continue
            seen.add(value)
            yield format(value, f"0{slot_count}b")

    def _normalize_fixed_channel_pattern(self, pattern: str | None, slot_count: int) -> str | None:
        if not pattern:
            return None
        cleaned = "".join(char for char in str(pattern).strip() if char in {"0", "1"})
        if not cleaned:
            return None
        return cleaned[:slot_count].ljust(slot_count, "0")

    def _build_pattern_iterator(
        self,
        slot_count: int,
        activation_mode: str,
        max_patterns: int,
        fixed_channel_pattern: str | None,
        randomized: bool,
        rng: random.Random,
    ):
        if activation_mode == "fixed_pattern" and fixed_channel_pattern:
            return iter([fixed_channel_pattern])
        if activation_mode == "binary_enumeration":
            return self._binary_patterns(
                slot_count,
                max_patterns,
                randomized=randomized,
                rng=rng,
            )
        return self._binary_patterns(
            slot_count,
            min(max_patterns, 16),
            randomized=randomized,
            rng=rng,
        )

    def _normalize_blueprint(
        self,
        blueprint: Dict[str, Any],
        task: Dict[str, Any],
        path_bundle: Dict[str, Any],
        reflection: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        inventory = task.get("transmitter_inventory")
        if not isinstance(inventory, dict) or not inventory:
            inventory = self._default_inventory()
        inventory = {str(key).strip().lower(): int(value) for key, value in inventory.items()}

        requested_modulations = {
            str(item).strip().upper()
            for item in task.get("modulations", ["QPSK", "16QAM"])
            if str(item).strip().upper() in {"QPSK", "16QAM", "64QAM"}
        }
        supported_modes = self._load_transceiver_modes()
        available_tx_types = [tx for tx, count in inventory.items() if count > 0 and tx in supported_modes]
        default_engine = task.get("preferred_engine")
        if default_engine not in {"gnpy", "opticommpy"}:
            default_engine = "gnpy"

        planner_updates = (reflection or {}).get("planner_updates", {})
        power_sweep = planner_updates.get("power_sweep_dbm") if isinstance(planner_updates, dict) else None
        planner_requested_power_sweep = isinstance(power_sweep, list) and bool(power_sweep)
        if not isinstance(power_sweep, list) or not power_sweep:
            power_sweep = task.get("power_sweep_dbm")
        task_requested_power_sweep = isinstance(task.get("power_sweep_dbm"), list) and bool(task.get("power_sweep_dbm"))
        if not isinstance(power_sweep, list) or not power_sweep:
            power_sweep = [self._default_launch_power()]
        allow_path_power_override = planner_requested_power_sweep or task_requested_power_sweep

        normalized_paths = []
        requested_paths = blueprint.get("paths")
        if not isinstance(requested_paths, list) or not requested_paths:
            requested_paths = [{"destination": path["destination"]} for path in path_bundle["paths"]]

        for path in path_bundle["paths"]:
            path_match = next(
                (item for item in requested_paths if str(item.get("destination", "")).strip().lower() == str(path["destination"]).strip().lower()),
                None,
            )
            if path_match is None:
                continue

            tx_types = path_match.get("transmitter_types")
            if not isinstance(tx_types, list) or not tx_types:
                tx_types = available_tx_types
            tx_types = [str(item).strip().lower() for item in tx_types if str(item).strip().lower() in available_tx_types]
            if not tx_types:
                continue

            modulations = path_match.get("modulations")
            if not isinstance(modulations, list) or not modulations:
                modulations = sorted(requested_modulations)
            modulations = [str(item).strip().upper() for item in modulations if str(item).strip().upper() in requested_modulations]
            if not modulations:
                continue

            normalized_paths.append(
                {
                    "destination": path["destination"],
                    "transmitter_types": tx_types,
                    "modulations": modulations,
                    "channel_slots": int(path_match.get("channel_slots", task.get("channel_slots", 8))),
                    "channel_activation_mode": str(path_match.get("channel_activation_mode", task.get("channel_activation_mode", "representative"))).strip().lower(),
                    "fixed_channel_pattern": path_match.get("fixed_channel_pattern", task.get("fixed_channel_pattern")),
                    "max_channel_patterns": int(path_match.get("max_channel_patterns", task.get("max_channel_patterns", 16))),
                    "preferred_engine": path_match.get("preferred_engine", default_engine),
                    "power_sweep_dbm": [
                        float(value)
                        for value in (
                            path_match.get("power_sweep_dbm")
                            if allow_path_power_override and isinstance(path_match.get("power_sweep_dbm"), list) and path_match.get("power_sweep_dbm")
                            else power_sweep
                        )
                    ],
                    "notes": str(path_match.get("notes", blueprint.get("notes", task.get("notes", "")))).strip(),
                }
            )

        return {"paths": normalized_paths}

    def _fallback_blueprint(
        self,
        task: Dict[str, Any],
        path_bundle: Dict[str, Any],
        reflection: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        _ = reflection
        inventory = task.get("transmitter_inventory")
        if not isinstance(inventory, dict) or not inventory:
            inventory = self._default_inventory()
        tx_types = [str(key).strip().lower() for key, value in inventory.items() if int(value) > 0]
        return {
            "paths": [
                {
                    "destination": path["destination"],
                    "transmitter_types": tx_types,
                    "modulations": task.get("modulations", ["QPSK", "16QAM"]),
                    "channel_slots": int(task.get("channel_slots", 8)),
                    "channel_activation_mode": str(task.get("channel_activation_mode", "representative")).strip().lower(),
                    "fixed_channel_pattern": task.get("fixed_channel_pattern"),
                    "max_channel_patterns": int(task.get("max_channel_patterns", 16)),
                    "preferred_engine": task.get("preferred_engine", "gnpy"),
                    "power_sweep_dbm": task.get("power_sweep_dbm") or [self._default_launch_power()],
                    "notes": str(task.get("notes", "")).strip(),
                }
                for path in path_bundle["paths"]
            ]
        }

    def _ask_llm_for_blueprint(
        self,
        task: Dict[str, Any],
        path_bundle: Dict[str, Any],
        reflection: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        supported_modes = self._load_transceiver_modes()
        reflection_view = {
            "action": (reflection or {}).get("action"),
            "message": (reflection or {}).get("message"),
            "planner_updates": (reflection or {}).get("planner_updates", {}),
            "scenario_power_overrides": (reflection or {}).get("scenario_power_overrides", {}),
        }
        prompt = f"""
You are the Scenario Expander Agent in a multi-agent optical network simulation system.

Use the task, the resolved physical paths, and reflection feedback to produce a compact scenario blueprint.
Do not brute-force-write every final scenario row yourself. Return a path-level blueprint that the execution layer can expand.

Rules:
- Use the physical paths exactly as provided.
- Use only transmitter types that exist in the requested inventory and in the equipment file.
- Use only supported modulations already requested by the planner.
- If the task says binary enumeration across 8 slots, keep "channel_activation_mode" as "binary_enumeration" and "max_channel_patterns" as 256.
- If the planner provides a fixed all-on pattern such as "111", preserve it with "channel_activation_mode": "fixed_pattern".
- For the GNPy raw dataset workflow, keep the engine as "gnpy".
- If no power sweep was requested, keep a single default power point instead of inventing a wide sweep.
- Return only JSON.

Task:
{json.dumps(task, indent=2)}

Resolved physical-layer paths:
{json.dumps(path_bundle, indent=2)}

Available transmitter modes from the equipment file:
{json.dumps(supported_modes, indent=2)}

Reflection feedback:
{json.dumps(reflection_view, indent=2)}

Return JSON:
{{
  "notes": "...",
  "paths": [
    {{
      "destination": "...",
      "transmitter_types": ["voyager"],
      "modulations": ["QPSK", "16QAM"],
      "channel_slots": 8,
      "channel_activation_mode": "binary_enumeration",
      "fixed_channel_pattern": null,
      "max_channel_patterns": 256,
      "preferred_engine": "gnpy",
      "power_sweep_dbm": [-5.5],
      "notes": "..."
    }}
  ]
}}
"""
        response = call_llm(prompt)
        return parse_llm_json(response)

    def expand(
        self,
        task: Dict[str, Any],
        path_bundle: Dict[str, Any],
        reflection: Dict[str, Any] | None = None,
        iteration: int = 1,
    ) -> List[Dict[str, Any]]:
        supported_modes = self._load_transceiver_modes()
        path_lookup = {str(path["destination"]).strip().lower(): path for path in path_bundle["paths"]}
        scenario_power_overrides = {
            str(key): [float(value) for value in values]
            for key, values in (reflection or {}).get("scenario_power_overrides", {}).items()
            if isinstance(values, list) and values
        }

        if str(task.get("preferred_engine") or "").strip().lower() == "opticommpy":
            blueprint = self._fallback_blueprint(task, path_bundle, reflection)
        else:
            try:
                blueprint = self._ask_llm_for_blueprint(task, path_bundle, reflection)
            except Exception:
                blueprint = self._fallback_blueprint(task, path_bundle, reflection)
        blueprint = self._normalize_blueprint(blueprint, task, path_bundle, reflection)

        scenarios: List[Dict[str, Any]] = []
        scenario_index = 1
        max_dataset_points = max(1, int(task.get("max_dataset_points", 50000)))
        truncated = False
        randomize_order = True
        random_seed = task.get("random_seed")
        rng = random.Random(random_seed) if random_seed is not None else random.Random()
        inventory = {
            str(key).strip().lower(): int(value)
            for key, value in (task.get("transmitter_inventory") or {}).items()
        }
        family_configs: List[Dict[str, Any]] = []

        for family in blueprint["paths"]:
            path = path_lookup.get(str(family["destination"]).strip().lower())
            if path is None:
                continue

            slot_count = max(1, int(family["channel_slots"]))
            activation_mode = str(family["channel_activation_mode"]).strip().lower()
            max_patterns = max(1, int(family["max_channel_patterns"]))
            fixed_channel_pattern = self._normalize_fixed_channel_pattern(family.get("fixed_channel_pattern"), slot_count)

            for transmitter_type in family["transmitter_types"]:
                modes = supported_modes.get(transmitter_type, [])
                for mode in modes:
                    if mode["modulation"] not in family["modulations"]:
                        continue
                    family_configs.append(
                        {
                            "path": path,
                            "family": family,
                            "mode": mode,
                            "transmitter_type": transmitter_type,
                            "slot_count": slot_count,
                            "activation_mode": activation_mode,
                            "channel_patterns": self._build_pattern_iterator(
                                slot_count=slot_count,
                                activation_mode=activation_mode,
                                max_patterns=max_patterns,
                                fixed_channel_pattern=fixed_channel_pattern,
                                randomized=randomize_order,
                                rng=rng,
                            ),
                        }
                    )

        if randomize_order:
            rng.shuffle(family_configs)

        active_families = family_configs
        while active_families and len(scenarios) < max_dataset_points:
            next_round: List[Dict[str, Any]] = []
            for config in active_families:
                try:
                    channel_pattern = next(config["channel_patterns"])
                except StopIteration:
                    continue

                path = config["path"]
                family = config["family"]
                mode = config["mode"]
                transmitter_type = config["transmitter_type"]
                slot_count = int(config["slot_count"])
                activation_mode = str(config["activation_mode"])

                family_key = self._scenario_family_key(path["destination"], mode, channel_pattern)
                family_power_sweep = scenario_power_overrides.get(family_key, family["power_sweep_dbm"])
                for power_dbm in family_power_sweep:
                    if len(scenarios) >= max_dataset_points:
                        truncated = True
                        break
                    scenarios.append(
                        {
                            "scenario_id": f"iter-{iteration}-scenario-{scenario_index}",
                            "network": path["network"],
                            "source": path["source"],
                            "destination": path["destination"],
                            "path_nodes": path["nodes"],
                            "spans": path["spans"],
                            "distance": path["total_distance_km"],
                            "task": task.get("task", "qot_estimation"),
                            "kpis": task.get("kpis", []),
                            "power": float(power_dbm),
                            "modulation": mode["modulation"],
                            "channels": channel_pattern.count("1"),
                            "preferred_engine": family["preferred_engine"],
                            "transmitter_type": transmitter_type,
                            "transmitter_inventory_available": inventory.get(transmitter_type, 0),
                            "channel_slots": slot_count,
                            "channel_pattern": channel_pattern,
                            "channel_activation_mode": activation_mode,
                            "scenario_family_key": family_key,
                            "baud_rate_gbaud": mode["baud_rate_gbaud"],
                            "bit_rate_gbps": mode["bit_rate_gbps"],
                            "min_spacing_ghz": mode["min_spacing_ghz"],
                            "tx_osnr_db": mode["tx_osnr_db"],
                            "notes": family["notes"] or f"LLM-expanded dataset scenario for {transmitter_type} {mode['modulation']}.",
                        }
                    )
                    scenario_index += 1

                if truncated:
                    break
                next_round.append(config)

            if truncated:
                break
            if randomize_order:
                rng.shuffle(next_round)
            active_families = next_round

        if not scenarios:
            raise ValueError("Scenario expansion produced no executable scenarios.")
        if truncated:
            for scenario in scenarios:
                scenario["dataset_truncated"] = True
                scenario["dataset_limit"] = max_dataset_points
        return scenarios
