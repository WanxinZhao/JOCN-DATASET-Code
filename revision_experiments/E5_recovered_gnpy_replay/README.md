# E5: recovered-source GNPy replay

This experiment separates two questions that were conflated in E4.

1. **Historical-output compatibility.** The archived legacy equipment and spectrum JSON files are replayed with official GNPy `v2.12`, commit `7ce665010970f57e46672db1ec0864ae448077e6`, under Python 3.9.13. This tagged version reproduces the archived CLI behaviour, but its agreement does not prove that the unrecorded original runtime used that exact commit.
2. **Current strict-schema check.** The same 96 stratified cases are run with GNPy 2.14.2 and `oopt-gnpy-libyang` 0.0.14. A copied equipment file is made schema-valid, and disabled `-120 dBm` placeholders are removed. For one-channel cases only, a `-40 dBm` guard is added because the 2.14.2 EDFA interpolation implementation assumes at least two frequencies; the guard is excluded from reported metrics.

## Results

- Design: 4 destinations × 2 modulation/grid configurations × 3 launch powers × 4 active-channel counts = 96 cases per arm.
- Historical arm: 96/96 successful. Mean/max absolute GSNR difference from the archived result is 0.000148/0.000500 dB; all 96 are within 0.0051 dB.
- Strict arm: 96/96 successful. Mean/max absolute GSNR difference is 0.628141/1.434500 dB; mean/max absolute OSNR difference is 0.208547/0.928500 dB.
- The strict-arm difference is route dependent: Bradley Stoke (one span) differs by 0.0172 dB on average, whereas Froxfield, Powergate and Reading differ by about 0.83 dB on average.
- Both arms still emit EDFA-below-minimum-gain warnings in every case, and the tested source-to-destination paths emit a ROADM target-power warning. Successful execution therefore establishes numerical replay and version sensitivity, not warning-free physical calibration.

## Schema corrections in the strict arm

- `Span[0].target_extended_gain`: 30 → 2.5.
- `Span[0].max_loss`: 0 → 28.
- Removed the unused duplicate 64-Gbaud QPSK and 16QAM modes from the `teraflex` transceiver because the YANG list key is `format`. Dataset cases use the `voyager` transceiver.
- Removed disabled `-120 dBm` spectrum entries. Single-channel inputs receive one explicitly labelled `-40 dBm` numerical guard.

## Files

- `replay_results_96_cases.csv`: case-level outputs and differences.
- `replay_summary.csv`: arm-level aggregate results.
- `difference_by_factor.csv`: route, modulation/grid, power and load breakdowns.
- `historical_v2.12_logs.jsonl` and `strict_v2.14.2_logs.jsonl`: commands, stdout and stderr.
- `configuration_changes.json`: machine-readable equipment edits.
- `source/`: topology, original/fixed equipment, and recovered orchestration-source snapshot.
- `source_snapshot_manifest.csv` and `provenance.json`: hashes and version evidence.
- `requirements-*-freeze.txt`: the two observed Python environments.

Run the experiment with `../run_e5_recovered_gnpy.py`. The driver verifies the pinned v2.12 commit before simulation. Runtime environments are intentionally not committed.
