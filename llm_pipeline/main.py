import argparse
import sys

from orchestrator.orchestrator import Orchestrator
from orchestrator.physical_layer_store import PhysicalLayerStore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the optical DT orchestration.")
    parser.add_argument(
        "--request",
        help="Run non-interactively with this exact user request.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=2,
        help="Maximum planner/expander/reflection iterations (default: 2).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = _parse_args()

    print("=" * 84)
    print("Optical DT Request Console")
    print("=" * 84)
    print(f"Physical-layer source: {PhysicalLayerStore().describe()}")

    user_request = (args.request or input("Enter your request: ")).strip()
    if not user_request:
        print("No request entered. Exiting without running orchestration.")
        raise SystemExit(0)

    orchestrator = Orchestrator(max_iterations=max(1, args.max_iterations))
    result = orchestrator.run(user_request)

    print()
    print("=" * 84)
    print("Final Report")
    print("=" * 84)
    print(result["report"])

    print()
    print("=" * 84)
    print("Artifacts")
    print("=" * 84)
    print(f"scenarios_csv : {result['artifacts']['scenarios_csv']}")
    print(f"channel_gsnr_csv: {result['artifacts']['channel_gsnr_csv']}")
    print(f"records_jsonl: {result['artifacts']['records_jsonl']}")
    print(f"summary_json : {result['artifacts']['summary_json']}")
    print(f"report_txt   : {result['artifacts']['report_txt']}")
    print(f"llm_trace_dir: {result['artifacts']['llm_trace_dir']}")
