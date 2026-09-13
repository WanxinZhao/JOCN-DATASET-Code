# E6: OpenAI trace reproduction

## Purpose

This experiment reruns the recovered multi-agent workflow while preserving the
runtime-rendered API requests and complete OpenAI response objects. It is new
reproduction evidence and must not be described as the unavailable original
historical API trace.

## Reproduction configuration

- Provider for this new run: OpenAI (explicitly set by the E6 launcher; this is
  not evidence of the provider used in the historical run).
- Upstream/recovered implementation reference:
  [`WanxinZhao/ECOC2026_Code@57b04f1`](https://github.com/WanxinZhao/ECOC2026_Code/commit/57b04f1).
- Model requested for the new run: `gpt-4o-mini-2024-07-18` (fixed snapshot).
- Recovered source aliases: OpenAI `gpt-4o-mini` and Gemini `gemini-1.5-pro`
  defaults; the historical provider and model actually used are unknown.
- Temperature: `0.2`.
- Timeout: `90 s`.
- Maximum attempts per provider: `3`.
- Strict LLM mode: enabled; an API failure cannot be hidden by a rule fallback.
- Topology: the audited `E5_recovered_gnpy_replay/source/NDFF_Testbed.json`.
- Equipment: the audited schema-fixed
  `E5_recovered_gnpy_replay/source/eqpt_config_NDFF_schema_fixed.json`.
- Pricing snapshot date: `2026-09-13` (checked against the official model page on the run date).
- Pricing per 1M text tokens: input `$0.15`, cached input `$0.075`, output `$0.60`,
  from the [official OpenAI GPT-4o mini model page](https://developers.openai.com/api/docs/models/gpt-4o-mini).

## Output layout

The local launcher writes every run to `runs/<run_id>/`. The `llm_trace`
subdirectory contains:

- `run_manifest.json`;
- `requests/*.json`;
- `responses/*.json`;
- `calls.jsonl`;
- `call_summary.csv`;
- `errors.jsonl` when an attempt fails.

The manifest also records the source commit and dirty state, Python and package
versions, input hashes, the exact user request, whether any API key was configured
(never the key itself), runtime settings, aggregate token use and estimated cost.
Human edits, interruptions, or restarts are not detected automatically and must
be manually documented if they occur.

## Current completed reproduction

The completed new run is `20260913_134829_038619`. It is a **new reproduction**
and does not recover the unavailable historical provider/model trace. The
trace and generated data are under the ignored local path
`runs/20260913_134829_038619/`; the full run record and recovery history are in
[`11_E6_20260913_resumed_full_run_report.md`](../../revision_documents/11_E6_20260913_resumed_full_run_report.md).
The complete persistent archive—including the run, both raw GNPy runtime
directories, the relevant source snapshot, inputs, and evidence reports—is
available as [`E6_completed_reproduction_20260913_134829_038619.tar.gz`](E6_completed_reproduction_20260913_134829_038619.tar.gz).
The runtime checkout recorded in its manifest was at local commit
`27b4e5903dde426e90f0bb621636a8c551bae25a` with a dirty worktree; the upstream
reference above is not a substitute for that exact working-copy state.

- 8,192 scenarios: 2,048 in iteration 1 and 6,144 reflection-expanded scenarios
  in iteration 2.
- Final statuses: 8,160 `ok`, 32 expected `no_signal` all-off patterns, and no
  residual execution errors. All nine original single-channel GNPy errors were
  replayed successfully after the single-channel fix.
- Each of four Bristol-origin destinations and each modulation covered all 256
  eight-slot binary patterns. Iteration 1 used -5.5 dBm; iteration 2 used
  -5.5/-5.0/-4.5 dBm.
- Six OpenAI calls / six attempts, no retries or API failures; model returned:
  `gpt-4o-mini-2024-07-18`; 108,308 total tokens; estimated text-token cost
  `$0.01726635` using the dated pricing snapshot. Full rendered requests and raw
  responses are in `llm_trace/`.
- Run start-to-finish wall-clock interval was 2 h 1 min 25.3 s and includes the
  user interruption and resume; uninterrupted compute duration was not
  separately measured.

The final Reflection Agent action was `refine`, but the configured maximum was
two iterations; no third iteration was launched. The run therefore completed
its configured two-iteration batch, not a convergence/stopping criterion. The
previous reports 09 and 10 remain historical records of the earlier incomplete
attempt and bounded repair stage; report 11 is authoritative for the completed
run.

## Repairs and validation

The project-local GNPy package is installed editable in the isolated
`/tmp/jocn-e6-venv` environment. Disabled slots are omitted from the propagated
spectrum, the CLI reads received power from `Channel.signal`, and the local
GNPy single-channel case uses the declared `spectral_info.slot_width` rather
than indexing a nonexistent second channel frequency. The six focused
execution/audit unit tests pass. The nine rows that had failed before that
single-channel correction now return `ok`; the original failure records remain
in the immutable first execution log, and their corrected outputs are recorded
as recovery entries.

The completed dataset files are `scenarios.csv`, `channel_gsnr.csv`,
`records.jsonl`, `summary.json`, and `report.txt`. Raw GNPy request/result/
spectrum files remain in the distinct original and resumed runtime directories
named in `summary.json` and `llm_trace/run_manifest.json`. The initial and
intermediate recovery diagnostics are preserved in `agent_logs/` and are not
counted as simulator results when they have no simulation configuration.
