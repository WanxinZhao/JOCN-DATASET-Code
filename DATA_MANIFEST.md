# Data manifest

The committed experiment data fall into three evidence classes.

| Directory | Role | Main contents |
| --- | --- | --- |
| `E3_llm_baseline` | LLM-output audit | Published report, iteration audit, deterministic comparison, prompt inventory and provenance |
| `E4_gnpy_audit` | Scenario-level supporting data | Archived published scenarios/artifacts, 6,144-row deterministic configuration tables, GNPy comparisons and warning audits |
| `E5_recovered_gnpy_replay` | Independent numerical replay | NDFF topology/equipment files, recovered prompt/orchestrator snapshot, 96 stratified inputs, logs and numerical results |
| `E6_llm_trace_reproduction` | Future OpenAI evidence | Reproduction configuration, preflight record and launcher; no fabricated historical API trace |

No API key, `.env` file, virtual environment, Python cache, local editor state,
or unrelated full telemetry archive is included. The E4 `artifacts.zip` is kept
because it is the archived result bundle from which the scenario-level audit is
derived; it is a normal Git object below GitHub's single-file size limit.

