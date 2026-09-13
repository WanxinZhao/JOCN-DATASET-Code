# E4: GNPy uniqueness and correctness audit

The audit separates execution records from unique configurations, compares the published scenario set with a deterministic 6,144-configuration ground truth, scans all published result artifacts for simulator warnings, and independently reruns 96 stratified configurations with GNPy 2.14.2.

The independent rerun uses the public `NDFF_Testbed.json` and `eqpt_config_NDFF.json`; it does not reuse the per-scenario request/result files produced by the agent workflow. All 96 requested runs fail before propagation because the public equipment file violates the schema accepted by PyPI GNPy 2.14.2 (out-of-range span values and duplicate transceiver modes). A separate compatibility check gives the same failure under GNPy 2.13.0. The public repository does not pin the original GNPy commit, so the published numerical outputs cannot currently be independently regenerated from the supplied inputs.
