# E6 GNPy execution repair and bounded verification

Date: 2026-09-13
Scope: diagnose and repair the GNPy installation/configuration blockers and
verify one representative configuration. This report supplements, and does
not overwrite, the historical record in
[`09_E6_20260913_new_run_trace_report.md`](09_E6_20260913_new_run_trace_report.md).

## Outcome

The two reported blockers have different causes and are both addressed:

1. The GNPy CLI could not import the project-local package because GNPy was
   declared as a local project package but had not been installed in the
   Python environment used to launch the child process.
2. Disabled spectrum slots were serialized as `tx_power_dbm=-120.0`. The
   repository's GNPy YANG schema only allows `tx_power_dbm` from `-60` to
   `60 dBm`; moreover, an inactive slot should not be represented as a weak
   active carrier.

A bounded single-scenario GNPy run now completes. The full LLM workflow and
2,048-scenario batch have **not** been restarted, so this is not a claim that
the full workflow is complete.

## Diagnosis and changes

### 1. Local GNPy installation / import path

`llm_pipeline/setup.cfg` declares the local distribution as `gnpy`. The
execution agent launches `llm_pipeline/tools/cli_examples.py` as a script
(`llm_pipeline/orchestrator/execution_agent.py`, `_run_gnpy_cli`, around line
337). In that invocation mode, Python's first script import directory is
`tools/`, not the project root. Without an installed distribution or an
explicit project-root `PYTHONPATH`, `import gnpy` in the CLI fails. The
original interrupted attempt therefore failed before GNPy could validate or
run its configuration.

Installed the checkout-local package editable into the isolated environment
`/tmp/jocn-e6-venv`, without changing the system Python:

- Python `3.12.13`
- local `gnpy` distribution `0.0.1.dev6`, editable source
  `/tmp/JOCN-DATASET-Code-audit/llm_pipeline`
- NumPy `1.26.4` (within the project's declared `<2` constraint)
- `oopt-gnpy-libyang` `0.0.14`

The direct CLI import check passes in this environment. Installing GNPy fixes
the import failure; it does not by itself fix the invalid inactive-slot
configuration described below.

### 2. Invalid inactive-slot representation

`ExecutionAgent._build_spectrum_payload()` in
`llm_pipeline/orchestrator/execution_agent.py` previously emitted every slot
in the channel pattern and assigned `-120.0 dBm` to slots marked `0`. The
checked-in schema `llm_pipeline/gnpy/yang/gnpy-eqpt-config@2025-05-26.yang`
defines `tx_power_dbm` with range `-60 .. 60` (line 679 onward), so GNPy
correctly rejects `-120.0 dBm` with a YANG validation error.

The implementation now omits disabled slots from the propagated spectrum.
Active slots retain their original frequency positions and `slot-N` labels,
so gaps in a pattern remain gaps rather than shifting later channels. An
all-zero pattern continues to be handled by the existing `no_signal` path.
Patterns containing characters other than `0` and `1` now fail early with a
clear configuration error. This is both schema-valid and a more faithful
representation of a switched-off channel; clamping `-120 dBm` to `-60 dBm`
would still incorrectly create an active carrier.

### 3. CLI result extraction defect exposed after propagation

Once the import and schema blockers were removed, a representative GNPy
propagation reached the CLI's channel-reporting code and exposed an additional
integration error: `tools/cli_examples.py` read
`final_carrier.power.signal`, but `infos.carriers` supplies a
`gnpy.core.info.Channel` record whose received signal is `final_carrier.signal`
in watts. The CLI now converts that value to milliwatts before the existing
dB conversion, yielding dBm. Without this correction propagation ran, but the
CLI failed while extracting/printing its successful result.

## Verification performed

### Automated tests and import check

Command (from `llm_pipeline/`):

```text
/tmp/jocn-e6-venv/bin/python -m unittest tests.test_execution_agent_spectrum tests.test_llm_audit
```

Result: **6 tests passed**. The three new focused tests verify active-slot
selection and original frequency/label preservation, all-off patterns, and
rejection of invalid pattern symbols. The existing three LLM audit tests also
pass. In addition, invoking `tools/cli_examples.py --help` with the isolated
environment succeeds without setting `PYTHONPATH`.

### One representative GNPy execution

Used a configuration from the preserved E6 attempt with pattern
`10101011`, eight declared slots, five active channels, and four optical
spans. A new scenario identifier was used so the earlier interrupted run
artifacts were not overwritten. The ExecutionAgent result is `status=ok`,
`backend=real`, with `channels_simulated=5` and `spans_simulated=4`. The CLI
returned parsed per-channel results and aggregate received-power/OSNR/GSNR
metrics. These are smoke-test outputs, not replacement dataset results or a
full-batch rerun.

GNPy emitted warnings for missing optional equipment attributes (defaults were
applied), effective amplifier gains below the configured 15 dB minimum for
some nodes, and a ROADM target-power condition. The scenario completed, but
these configuration warnings are retained as caveats; this repair did not
change amplifier/ROADM settings or claim that those warnings are immaterial to
the model.

## Files changed for this repair

- `llm_pipeline/orchestrator/execution_agent.py` — validate channel-pattern
  symbols and omit disabled spectrum slots.
- `llm_pipeline/tools/cli_examples.py` — use the `Channel.signal` field for
  received signal power.
- `llm_pipeline/tests/test_execution_agent_spectrum.py` — targeted unit tests.
- `revision_experiments/E6_llm_trace_reproduction/README.md` — distinguish the
  historical interruption from subsequent bounded repairs.

The isolated Python environment is outside the repository. Existing E6 API
trace files, original scenario/source data, prior GNPy outputs, and legacy
results were not overwritten. No LLM API calls, full workflow rerun, dataset
regeneration, commit, or push occurred in this repair stage.

## Why the complete workflow remains incomplete

The earlier process had already stopped after two successful LLM calls and
did not reach completion. Installing/fixing GNPy does not resume that
interrupted orchestration automatically, and the original call sequence is
not replayed from its prior in-memory state. This stage only repaired the
execution path and ran one bounded GNPy smoke configuration; it did not make
the additional LLM calls needed to generate/validate the full scenario batch.
Consequently the full E6 workflow remains incomplete until explicitly
restarted as a new, separately logged reproduction run.
