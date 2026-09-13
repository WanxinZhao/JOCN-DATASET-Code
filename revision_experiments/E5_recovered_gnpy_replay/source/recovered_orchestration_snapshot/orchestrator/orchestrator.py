import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from orchestrator.execution_agent import ExecutionAgent
from orchestrator.physical_layer_store import PhysicalLayerStore
from orchestrator.planner_agent import PlannerAgent
from orchestrator.reflection_agent import ReflectionAgent
from orchestrator.report_agent import ReportAgent
from orchestrator.results_analysis_agent import ResultsAnalysisAgent
from orchestrator.scenario_expander_agent import ScenarioExpanderAgent


def _banner(title: str) -> None:
    line = "=" * 84
    print()
    print(line)
    print(title)
    print(line)


def _section(title: str) -> None:
    print()
    print(f"--- {title} " + "-" * max(8, 72 - len(title)))


def _save_run_artifacts(result: Dict[str, Any], base_dir: str = "data/runs") -> Dict[str, str]:
    run_id = str(result.get("run_id") or datetime.now().strftime("%Y%m%d_%H%M%S"))
    run_dir = Path(base_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    scenarios_csv = run_dir / "scenarios.csv"
    channel_gsnr_csv = run_dir / "channel_gsnr.csv"
    records_jsonl = run_dir / "records.jsonl"
    summary_json = run_dir / "summary.json"
    report_txt = run_dir / "report.txt"

    for record in result.get("records", []):
        scenario = record.get("scenario", {})
        output = record.get("output", {})
        artifacts = output.get("artifacts", {})
        if not isinstance(artifacts, dict) or not artifacts:
            continue

        scenario_dir = run_dir / "artifacts" / str(scenario.get("scenario_id", "unknown"))
        scenario_dir.mkdir(parents=True, exist_ok=True)
        copied_artifacts: Dict[str, Any] = {}
        for key, value in artifacts.items():
            if not isinstance(value, str):
                copied_artifacts[key] = value
                continue
            src = Path(value)
            if not src.exists() or not src.is_file():
                copied_artifacts[key] = value
                continue
            dst = scenario_dir / src.name
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            copied_artifacts[key] = str(dst)
        output["artifacts"] = copied_artifacts

    scenarios_csv.write_text(result.get("report_csv", ""), encoding="utf-8")
    channel_gsnr_csv.write_text(result.get("channel_report_csv", ""), encoding="utf-8")

    with records_jsonl.open("w", encoding="utf-8") as handle:
        for record in result.get("records", []):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary_payload = {
        "run_id": run_id,
        "task": result.get("task"),
        "path_info": result.get("path_info"),
        "analysis": result.get("analysis"),
        "reflection": result.get("reflection"),
        "feasibility_profiles_used": result.get("feasibility_profiles_used", []),
        "iterations": result.get("iterations"),
        "agent_logs": result.get("agent_logs", {}),
        "report": result.get("report"),
    }
    summary_json.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    report_txt.write_text(result.get("report", ""), encoding="utf-8")

    return {
        "run_dir": str(run_dir),
        "scenarios_csv": str(scenarios_csv),
        "channel_gsnr_csv": str(channel_gsnr_csv),
        "records_jsonl": str(records_jsonl),
        "summary_json": str(summary_json),
        "report_txt": str(report_txt),
    }


class PhysicalLayerDB:
    """Load topology and equipment data from files or PostgreSQL."""

    def __init__(
        self,
        topology_path: str = "OFC_Testbed.json",
        equipment_path: str = "eqpt_config_NDFF.json",
    ) -> None:
        self.topology_path = Path(topology_path)
        self.equipment_path = Path(equipment_path)
        self._store = PhysicalLayerStore(
            topology_path=topology_path,
            equipment_path=equipment_path,
        )
        self._loaded = False
        self._elements: Dict[str, Dict[str, Any]] = {}
        self._next_hop: Dict[str, str] = {}
        self._city_by_uid: Dict[str, str] = {}
        self._fiber_defaults: Dict[str, Dict[str, Any]] = {}
        self._edfa_defaults: Dict[str, Dict[str, Any]] = {}
        self._source_uid_by_city: Dict[str, str] = {}

    def get_path(self, network: str, source: str, destination: str) -> Dict[str, Any]:
        self._ensure_loaded()
        paths = self.get_paths_from_source(network, source)
        destination_key = self._normalize_city(destination)
        for path in paths:
            if path["destination"] == destination_key:
                return path
        raise ValueError(f"No route found from {source} to {destination} in {network}.")

    def get_paths_from_source(self, network: str, source: str) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        source_key = self._normalize_city(source)
        if source_key not in self._source_uid_by_city:
            raise ValueError(f"No source node found for city: {source}")

        source_uid = self._source_uid_by_city[source_key]
        paths: List[Dict[str, Any]] = []
        current_uid = source_uid
        uid_path = [current_uid]
        seen_destinations = set()

        while current_uid in self._next_hop:
            current_uid = self._next_hop[current_uid]
            uid_path.append(current_uid)
            city = self._city_by_uid.get(current_uid)
            if city and city != source_key and city not in seen_destinations:
                seen_destinations.add(city)
                paths.append(self._build_path_record(network, source_key, city, uid_path.copy()))

        if not paths:
            raise ValueError(f"No routes found from {source} in {network}.")
        return paths

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        topology, equipment = self._store.load_topology_and_equipment()
        print(
            "Loaded physical-layer topology and equipment successfully "
            f"from {self._store.backend_label()}: {self._store.describe()}"
        )
        print(
            "  Topology elements="
            f"{len(topology.get('elements', []))}, connections={len(topology.get('connections', []))}; "
            f"equipment sections={len(equipment)}"
        )

        self._elements = {element["uid"]: element for element in topology.get("elements", [])}
        self._next_hop = {
            connection["from_node"]: connection["to_node"]
            for connection in topology.get("connections", [])
        }

        self._fiber_defaults = {
            item["type_variety"]: item for item in equipment.get("Fiber", [])
        }
        self._edfa_defaults = {
            item["type_variety"]: item for item in equipment.get("Edfa", [])
        }

        for uid, element in self._elements.items():
            if element.get("type") not in {"Transceiver", "Roadm"}:
                continue
            raw_city = element.get("metadata", {}).get("location", {}).get("city")
            if not raw_city:
                continue
            city = self._normalize_city(raw_city)
            self._city_by_uid[uid] = city
            if element.get("type") == "Transceiver" and uid.startswith("tx_"):
                self._source_uid_by_city[city] = uid

        self._loaded = True

    def _build_path_record(
        self,
        network: str,
        source: str,
        destination: str,
        uid_path: List[str],
    ) -> Dict[str, Any]:
        nodes: List[str] = [source]
        spans: List[Dict[str, Any]] = []
        pending_from = source
        pending_nf = 4.5
        pending_gain = 0.0

        for uid in uid_path[1:]:
            element = self._elements.get(uid, {})
            element_type = element.get("type")

            if element_type == "Edfa":
                pending_nf = self._resolve_edfa_nf(element)
                pending_gain = float(element.get("operational", {}).get("gain_target", 0.0))
            elif element_type == "Fiber":
                spans.append(self._build_span_record(element, pending_from, pending_nf, pending_gain))
            elif element_type == "Transceiver":
                city = self._city_by_uid.get(uid)
                if city and city != nodes[-1]:
                    if spans:
                        spans[-1]["to"] = city
                    nodes.append(city)
                    pending_from = city

        total_distance = round(sum(span["span_length_km"] for span in spans), 3)
        return {
            "network": network.upper(),
            "source": source,
            "destination": destination,
            "nodes": nodes,
            "spans": spans,
            "total_distance_km": total_distance,
        }

    def _build_span_record(
        self,
        fiber_element: Dict[str, Any],
        from_city: str,
        noise_figure_db: float,
        edfa_gain_db: float,
    ) -> Dict[str, Any]:
        params = fiber_element.get("params", {})
        type_variety = fiber_element.get("type_variety", "")
        defaults = self._fiber_defaults.get(type_variety, {})
        return {
            "from": from_city,
            "to": None,
            "span_length_km": float(params.get("length", 0.0)),
            "loss_coef_db_per_km": float(params.get("loss_coef", 0.2)),
            "noise_figure_db": noise_figure_db,
            "edfa_gain_db": edfa_gain_db,
            # GNPy stores dispersion in s/m^2. OptiCommPy expects ps/nm/km.
            # 1 ps/nm/km = 1e-6 s/m^2, so the conversion factor is 1e6.
            "dispersion_ps_per_nm_per_km": float(defaults.get("dispersion", 1.67e-05)) * 1e6,
            "gamma_per_w_per_km": float(defaults.get("gamma", 0.00127)) * 1e3,
            "pmd_coef": float(defaults.get("pmd_coef", 0.0)),
            "fiber_uid": fiber_element.get("uid"),
            "type_variety": type_variety,
        }

    def _resolve_edfa_nf(self, edfa_element: Dict[str, Any]) -> float:
        type_variety = edfa_element.get("type_variety", "")
        defaults = self._edfa_defaults.get(type_variety, {})
        if "nf0" in defaults:
            return float(defaults["nf0"])
        if "nf_min" in defaults and "nf_max" in defaults:
            return (float(defaults["nf_min"]) + float(defaults["nf_max"])) / 2.0
        return 4.5

    def _normalize_city(self, value: str) -> str:
        text = value.strip().lower().replace("_", " ")
        aliases = {
            "trx uob": "bristol",
            "uob": "bristol",
            "brd": "bradley stoke",
            "ffd": "froxfield",
            "rdg": "reading",
            "pgt": "powergate",
        }
        return aliases.get(text, text)


class Orchestrator:
    def __init__(self, max_iterations: int = 2) -> None:
        self.max_iterations = max_iterations
        self._log_dir = Path("orchestrator") / "logs"
        self._run_id = ""
        self._agent_log_paths: Dict[str, str] = {}
        self.planner = PlannerAgent()
        self.physical_layer_db = PhysicalLayerDB()
        self.expander = ScenarioExpanderAgent()
        self.executor = ExecutionAgent(log_hook=lambda message: self._log_agent("execution_agent", message))
        self.analysis = ResultsAnalysisAgent()
        self.reflection = ReflectionAgent()
        self.reporter = ReportAgent()

    def _start_run_logs(self) -> None:
        self._run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        agent_names = [
            "planner_agent",
            "scenario_expander_agent",
            "execution_agent",
            "results_analysis_agent",
            "reflection_agent",
            "report_agent",
            "orchestrator",
        ]
        self._agent_log_paths = {}
        for agent_name in agent_names:
            log_path = self._log_dir / f"{self._run_id}_{agent_name}.log"
            log_path.write_text("", encoding="utf-8")
            self._agent_log_paths[agent_name] = str(log_path)

    def _log_agent(self, agent_name: str, message: str) -> None:
        if not self._agent_log_paths:
            return
        log_path = Path(self._agent_log_paths[agent_name])
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    def _fetch_paths(self, task: Dict[str, Any]) -> Dict[str, Any]:
        if task.get("scope") == "all_paths_from_source":
            paths = self.physical_layer_db.get_paths_from_source(
                network=task["network"],
                source=task["source"],
            )
            return {
                "network": task["network"],
                "scope": task["scope"],
                "source": task["source"],
                "paths": paths,
            }

        path = self.physical_layer_db.get_path(
            network=task["network"],
            source=task["source"],
            destination=task["destination"],
        )
        return {
            "network": task["network"],
            "scope": task["scope"],
            "source": task["source"],
            "paths": [path],
        }

    def run(self, user_input: str) -> Dict[str, Any]:
        self._start_run_logs()
        _banner("Multi-Agent Optical DT Orchestration")
        print("Orchestrator received a new request")
        print(f"User input: {user_input}")
        self._log_agent("orchestrator", f"User input: {user_input}")

        all_records: List[Dict[str, Any]] = []
        iteration_log: List[Dict[str, Any]] = []
        active_feasibility_profiles: List[Dict[str, Any]] = []
        feasibility_profiles_used: List[Dict[str, Any]] = []
        reflection: Dict[str, Any] = {
            "action": "stop",
            "message": "No reflection executed yet.",
            "recommendations": [],
            "planner_updates": {},
        }
        task = self.planner.plan(user_input)
        self._log_agent("planner_agent", json.dumps(task, ensure_ascii=False))
        path_bundle = self._fetch_paths(task)
        self._log_agent("orchestrator", json.dumps(path_bundle, ensure_ascii=False))

        for iteration in range(1, self.max_iterations + 1):
            _section(f"Planner Agent Iteration {iteration}")
            if iteration > 1:
                print("Re-planning with reflection feedback...")
                task = self.planner.plan(user_input, reflection=reflection, prior_task=task)
                print(f"Refined task: {task}")
                self._log_agent("planner_agent", json.dumps({"iteration": iteration, "task": task}, ensure_ascii=False))
                path_bundle = self._fetch_paths(task)
            else:
                print("Parsing natural-language intent into a structured task...")
                print(f"Planned task: {task}")

            _section(f"Topology Iteration {iteration}")
            print("Retrieving topology and device/span parameters from the physical-layer data source...")
            print(f"Resolved {len(path_bundle['paths'])} path(s)")

            _section(f"Scenario Expander Iteration {iteration}")
            print("Expanding the task into executable simulation scenarios with the LLM...")
            scenarios = self.expander.expand(task, path_bundle, reflection=reflection if iteration > 1 else None, iteration=iteration)
            for scenario in scenarios:
                scenario["iteration"] = iteration
            print(f"Generated {len(scenarios)} scenarios")
            self._log_agent(
                "scenario_expander_agent",
                json.dumps({"iteration": iteration, "scenarios": scenarios}, ensure_ascii=False),
            )

            _section(f"Execution Iteration {iteration}")
            print("Running simulation scenarios...")
            records = [self.executor.execute(scenario) for scenario in scenarios]
            all_records.extend(records)

            _section(f"Analysis Iteration {iteration}")
            print("Ranking results and checking feasibility...")
            feasibility_profiles_used = list(active_feasibility_profiles)
            analysis = self.analysis.analyze(all_records, feasibility_profiles=feasibility_profiles_used)
            self._log_agent("results_analysis_agent", json.dumps({"iteration": iteration, "analysis": analysis}, ensure_ascii=False))
            print(
                f"Total={analysis['total_scenarios']} | "
                f"OK={analysis['ok_scenarios']} | "
                f"Errors={analysis['error_scenarios']} | "
                f"Best score={analysis['best_score']}"
            )

            _section(f"Reflection Iteration {iteration}")
            print("Reviewing whether another iteration is needed...")
            if str(task.get("preferred_engine") or "").strip().lower() == "opticommpy":
                reflection = {
                    "action": "stop",
                    "message": (
                        "Single-pass execution completed for the requested deterministic generation task; "
                        "no automatic reflection refinement was applied."
                    ),
                    "recommendations": [],
                    "planner_updates": {},
                    "scenario_power_overrides": {},
                    "feasibility_profiles": [],
                }
            else:
                try:
                    reflection = self.reflection.reflect(analysis, task)
                except Exception as exc:
                    reflection = {
                        "action": "stop",
                        "message": f"Reflection step failed and was skipped: {exc}",
                        "recommendations": [],
                        "planner_updates": {},
                        "scenario_power_overrides": {},
                        "feasibility_profiles": [],
                    }
            print(f"Reflection decision: {reflection}")
            self._log_agent("reflection_agent", json.dumps({"iteration": iteration, "reflection": reflection}, ensure_ascii=False))
            if isinstance(reflection.get("feasibility_profiles"), list):
                active_feasibility_profiles = list(reflection.get("feasibility_profiles", []))

            iteration_log.append(
                {
                    "iteration": iteration,
                    "task": task,
                    "scenario_count": len(scenarios),
                    "reflection": reflection,
                    "analysis": {
                        "total_scenarios": analysis["total_scenarios"],
                        "ok_scenarios": analysis["ok_scenarios"],
                        "error_scenarios": analysis["error_scenarios"],
                        "no_signal_scenarios": analysis["no_signal_scenarios"],
                        "best_score": analysis["best_score"],
                    },
                }
            )
            if reflection.get("action") != "refine":
                break

        _section("Report Agent")
        print("Building full CSV output and final report...")
        report_csv = self.reporter.build_csv(all_records, feasibility_profiles=feasibility_profiles_used)
        channel_report_csv = self.reporter.build_channel_csv(all_records)
        report = self.reporter.build_report(user_input, task, path_bundle, analysis, reflection, iteration_log)
        self._log_agent("report_agent", report)
        result = {
            "run_id": self._run_id,
            "task": task,
            "path_info": path_bundle,
            "records": all_records,
            "analysis": analysis,
            "reflection": reflection,
            "iterations": iteration_log,
            "report_csv": report_csv,
            "channel_report_csv": channel_report_csv,
            "report": report,
            "feasibility_profiles_used": feasibility_profiles_used,
            "agent_logs": self._agent_log_paths,
        }

        _section("Persistence")
        print("Persisting artifacts to disk...")
        result["artifacts"] = _save_run_artifacts(result)
        print(f"Artifacts saved in: {result['artifacts']['run_dir']}")
        return result
