# Historical LLM prompt evidence inventory

## Confirmed runtime fact

The authors confirm that the published historical multi-agent run used
**OpenAI**. The recovered OpenAI source path specifies `gpt-4o-mini`, but the
date-stamped model snapshot and byte-exact API trace were not archived.

## Prompt material present in the repository

| LLM role | Saved template | Values inserted at runtime |
|---|---|---|
| Planner | `../E5_recovered_gnpy_replay/source/recovered_orchestration_snapshot/orchestrator/planner_agent.py` | user request, prior task, previous reflection |
| Scenario Expansion | `../E5_recovered_gnpy_replay/source/recovered_orchestration_snapshot/orchestrator/scenario_expander_agent.py` | planned task, resolved paths, equipment modes, reflection feedback |
| Reflection | `../E5_recovered_gnpy_replay/source/recovered_orchestration_snapshot/orchestrator/reflection_agent.py` | current task and execution-analysis summary |
| Shared system message/API settings | `../E5_recovered_gnpy_replay/source/recovered_orchestration_snapshot/utils/llm.py` | OpenAI system message, model string, temperature, timeout, retry policy |

These are complete source templates, including rules and requested JSON
schemas. They are not merely short descriptions of the prompts.

The published report additionally preserves the original user request, the
planned-task summary, iteration counts, scenario totals, and the latest
Reflection message in `published_report.txt`. This is enough to explain the
workflow and partially inspect the values inserted into the templates.

## Material not present

No archive contains one record per API call with all of the following:

- the fully rendered prompt after inserting every runtime object;
- the raw OpenAI response and response identifier;
- the exact request timestamp and date-stamped model snapshot;
- whether a fallback path was entered or how many retry attempts occurred;
- prompt/completion token counts, API cost, or latency;
- errors and human interventions.

Consequently, the repository supports release of the complete prompt
**templates** and author-confirmed OpenAI provider. It does not support a claim
that the byte-exact historical prompt/response **trace** is available. A new
run could generate a new trace, but it would not recover the unlogged 2025/2026
historical API transactions.

## Recovered settings and observable artifact

- Provider used: OpenAI (author confirmed).
- Source model string: `gpt-4o-mini`.
- System instruction: optical-network expert.
- Temperature: 0.2.
- Timeout: 90 s by default.
- Attempts: at most three per request by default.
- Control-flow upper bound: six LLM call sites over two iterations.
- Observable output: 8,192 executions and 6,144 unique configurations.
- Deterministic baseline: the same 6,144-key set enumerated in 0.0063 s with
  no API call.
