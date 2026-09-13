import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)


def _plot_psd(data: Dict[str, Any], output_path: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    freq_hz = data.get("frequency_hz", [])
    psd_db = data.get("psd_db", [])
    if not freq_hz or not psd_db:
        return

    freq_thz = [item / 1e12 for item in freq_hz]
    plt.figure(figsize=(10, 4.8))
    plt.plot(freq_thz, psd_db, color="#1f4db3", linewidth=1.2)
    plt.xlabel("Frequency (THz)")
    plt.ylabel("PSD (dB)")
    plt.title(title)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close()


def _plot_constellation(data: Dict[str, Any], output_path: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    polarizations = data.get("polarizations", {})
    labels = [("pol_x", "Pol-X"), ("pol_y", "Pol-Y")]
    active = [item for item in labels if item[0] in polarizations]
    if not active:
        return

    fig, axes = plt.subplots(1, len(active), figsize=(6 * len(active), 6), squeeze=False)
    for index, (key, label) in enumerate(active):
        axis = axes[0, index]
        points = polarizations.get(key, {})
        i_values = points.get("i", [])
        q_values = points.get("q", [])
        if i_values and q_values:
            axis.scatter(i_values, q_values, s=5, alpha=0.28, color="#0f766e", edgecolors="none")
        else:
            axis.text(0.5, 0.5, "No samples", ha="center", va="center")
        axis.set_title(label)
        axis.set_xlabel("In-Phase")
        axis.set_ylabel("Quadrature")
        axis.grid(True, alpha=0.25)
        axis.axis("equal")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_triptych(data: Dict[str, Any], output_path: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    stages = list(data.get("stages", {}).items())
    if not stages:
        return

    fig, axes = plt.subplots(1, len(stages), figsize=(5.5 * len(stages), 5), squeeze=False)
    for index, (stage_name, points) in enumerate(stages):
        axis = axes[0, index]
        i_values = points.get("i", [])
        q_values = points.get("q", [])
        if i_values and q_values:
            axis.scatter(i_values, q_values, s=5, alpha=0.28, color="#a21caf", edgecolors="none")
        else:
            axis.text(0.5, 0.5, "No samples", ha="center", va="center")
        axis.set_title(stage_name)
        axis.set_xlabel("In-Phase")
        axis.set_ylabel("Quadrature")
        axis.grid(True, alpha=0.25)
        axis.axis("equal")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_eye(data: Dict[str, Any], output_path: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    traces = data.get("traces", [])
    sample_axis = data.get("sample_axis", [])
    plt.figure(figsize=(10, 4.8))
    if not traces or not sample_axis:
        plt.text(0.5, 0.5, data.get("warning", "No eye data"), ha="center", va="center")
    else:
        for trace in traces:
            plt.plot(sample_axis, trace, color="#1d4ed8", alpha=0.06, linewidth=0.8)
    plt.xlabel("Samples")
    plt.ylabel("Amplitude")
    plt.title(title)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close()


def _plot_known_files(plot_dir: Path, output_dir: Path) -> Dict[str, str]:
    mapping = {
        "tx_psd_data.json": ("tx_psd_replot.png", _plot_psd, "TX PSD"),
        "rx_psd_data.json": ("rx_psd_replot.png", _plot_psd, "RX PSD"),
        "tx_center_constellation_reference_data.json": (
            "tx_center_constellation_replot.png",
            _plot_constellation,
            "TX Center-Channel Constellation",
        ),
        "rx_frontend_constellation_data.json": (
            "rx_frontend_constellation_replot.png",
            _plot_constellation,
            "RX Frontend Constellation",
        ),
        "rx_after_edc_constellation_data.json": (
            "rx_after_edc_constellation_replot.png",
            _plot_constellation,
            "RX After EDC Constellation",
        ),
        "rx_after_equalizer_constellation_data.json": (
            "rx_after_equalizer_constellation_replot.png",
            _plot_constellation,
            "RX After Equalizer Constellation",
        ),
        "rx_after_cpr_constellation_data.json": (
            "rx_after_cpr_constellation_replot.png",
            _plot_constellation,
            "RX After CPR Constellation",
        ),
        "rx_dsp_triptych_data.json": ("rx_dsp_triptych_replot.png", _plot_triptych, "RX DSP Progression"),
        "rx_eye_data.json": ("rx_eye_replot.png", _plot_eye, "RX Eye Diagram"),
    }

    produced: Dict[str, str] = {}
    for filename, (output_name, plotter, title) in mapping.items():
        input_path = plot_dir / filename
        if not input_path.exists():
            continue
        output_path = output_dir / output_name
        plotter(_load_json(input_path), output_path, title)
        if output_path.exists():
            produced[filename] = str(output_path)
    return produced


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replot OptiCommPy artifact JSON files into custom PNG figures."
    )
    parser.add_argument(
        "plot_dir",
        help="Path to an OptiCommPy *_plots directory containing *_data.json files.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for regenerated figures. Defaults to <plot_dir>/replots.",
    )
    args = parser.parse_args()

    plot_dir = Path(args.plot_dir).resolve()
    if not plot_dir.exists() or not plot_dir.is_dir():
        raise SystemExit(f"Plot directory not found: {plot_dir}")

    output_dir = Path(args.output_dir).resolve() if args.output_dir else plot_dir / "replots"
    _ensure_output_dir(output_dir)
    produced = _plot_known_files(plot_dir, output_dir)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(produced, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Input plot dir : {plot_dir}")
    print(f"Output dir     : {output_dir}")
    print(f"Files created  : {len(produced)}")
    print(f"Manifest       : {manifest_path}")


if __name__ == "__main__":
    main()
