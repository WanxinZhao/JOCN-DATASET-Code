# E6 corrected second-round replay

## Outcome

The second-round simulation was regenerated after correcting a deterministic
post-processing defect. The replay completed **6,144/6,144** scenarios using
the power sweep returned consistently by the saved Reflection 1, Planner 2,
and Scenario Expander 2 responses: **−6.5, −6.0, and −5.5 dBm**.

## Cause and correction

The saved model responses agreed on the three-point power sweep. In the earlier
execution, iteration 1 had only one observed power (`−5.5 dBm`). Reflection
post-processing incorrectly treated this single point as a sweep boundary and
inferred family-level values `[−5.5, −5.0, −4.5] dBm`; these overrides then
took precedence over the Scenario Expander response. The cause was therefore
deterministic post-processing and precedence, not a Planner/Expander model
disagreement.

The corrected code in `llm_pipeline/orchestrator/reflection_agent.py` does not
infer a family sweep from fewer than two observed powers. Checks in
`llm_pipeline/orchestrator/scenario_expander_agent.py` reject disagreement
between Planner and Expander sweeps, Reflection overrides and the validated
sweep, or the final emitted family configurations and the requested set.
Regression coverage is in
`llm_pipeline/tests/test_power_sweep_consistency.py`.

## Execution and verification

- Saved LLM calls reused: Reflection 1 (`call_003`), Planner 2 (`call_004`),
  Scenario Expander 2 (`call_005`). **No new LLM API calls were made.**
- Iteration 1 was reused read-only: 2,048 records at `−5.5 dBm`.
- Iteration 2: 6,144 records, across 2,048 families; each family has all three
  powers. Each power occurs 2,048 times.
- Statuses: 6,120 `ok`; 24 `no_signal`; zero execution failures. The 24
  `no_signal` records all use the explicitly all-channels-off pattern
  `00000000` (eight families at each power).
- Requested versus executed launch power: zero mismatches across scenario,
  simulator configuration, and returned GNPy metrics.
- Combined corrected dataset: 8,192 records (2,048 unchanged iteration-1
  records plus 6,144 corrected iteration-2 records).
- Focused tests: 10 passed. Python compilation and `git diff --check` passed.
- Replay interval: 2026-09-14 09:12:23–10:37:32 UTC.

The detailed machine-readable manifest, full scenario/result records, channel
outputs, execution logs, GNPy request/result/spectrum artifacts, and reused
request/response JSON are in
`revision_experiments/E6_llm_trace_reproduction/E6_corrected_second_round_20260914.tar.gz`.
The archive is 21 MiB; SHA-256:
`6d62aedad432140cefb06a652f52fe6e4ecac26bc8d42ac758f4e38db836e52e`.
The manifest records the original input and saved-response hashes and the
SHA-256 digests of the two modified orchestration files. Its source commit is
the pre-fix base commit with a dirty worktree; the committed code in this
revision contains the recorded fixes.

## Scope and interpretation

This is a corrected deterministic replay from archived LLM suggestions, not a
new LLM generation or recovery of historical prompts, provider state, human
interventions, or API accounting. It corrects the executed second-round sweep;
it does not show that Reflection converged. The original archive is retained
unchanged as a historical record. For the corrected two-round result, use this
report and replay bundle instead of the old iteration-2 power values.
