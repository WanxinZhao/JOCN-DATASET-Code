# Historical LLM-run provenance: evidence boundary

## Verdict

The historical LLM provider, exact model snapshot, per-call activity and
operator actions are **unavailable from the preserved evidence**. Do not infer
them from the recovered source defaults, the published GNPy outputs, or a new
reproduction.

## What the recovered source establishes

The recovered `E5_recovered_gnpy_replay/source/recovered_orchestration_snapshot/utils/llm.py`
contains an OpenAI branch using the moving alias `gpt-4o-mini` (lines 56--71)
and a Gemini branch whose default alias is `gemini-1.5-pro` (lines 74--105).
The provider selector defaults to OpenAI but reads `LLM_PROVIDER` and can
fallback to the other provider when configured/available (lines 170--203).
The source also specifies a retry ceiling, not the observed retry count.
These are source-level facts only; the recovered directory has no Git metadata
that proves it is the exact historical checkout.

Recovered role prompt templates and JSON constraints are available. The
published report and archived `scenarios.csv`/`artifacts.zip` contain generated
scenario and GNPy output evidence, not a per-request API trace.

## Historical fields that remain unavailable

- Actual provider and model snapshot/version date.
- Fully rendered prompt for each call and each raw model response.
- Actual API call count, provider fallback events, retry count, and failures.
- Input/output tokens, API cost, per-call latency, and total workflow duration.
- Human edits, filtering, interruption, restart, and selection history.
- Independent repeated runs and single-LLM/no-Reflection ablation logs.

Unless contemporaneous records are later found, these fields should be stated
as unavailable/not reported. A new instrumented run cannot reconstruct them.

## New-run instrumentation boundary

E6 explicitly configures OpenAI with `gpt-4o-mini-2024-07-18` for a prospective
new run. A run was attempted on 2026-09-13 and two OpenAI calls completed; its
request/response, attempt, timing, model, token, and estimated-price records are
new-run evidence only. The workflow was interrupted during GNPy execution after
repeated import failures; a post-interruption smoke diagnostic exposed a second
GNPy spectrum-schema issue. See
[`09_E6_20260913_new_run_trace_report.md`](09_E6_20260913_new_run_trace_report.md).
None of this establishes the historical provider or history of operator
actions. Human interventions and external edits/restarts are not detected
automatically and require manual annotation.

## Revision claim boundary

The paper may describe the recovered multi-agent workflow as a feasibility
demonstration and report the deterministic configuration-set baseline. It
should not claim that the historical provider was OpenAI, nor claim LLM
superiority, lower cost/latency, or an architecture ablation without the
missing run-level evidence.
