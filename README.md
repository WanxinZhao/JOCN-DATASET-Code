# JOCN LLM dataset-generation code and evidence

This repository is the focused release of the LLM-related material used in the
JOCN dataset paper and its major revision. It contains the executable
multi-agent/OpenAI workflow, the data needed to audit it, and the revision
experiments that compare the LLM-generated scenario set with deterministic and
GNPy-based baselines.

## Repository layout

- `llm_pipeline/`: Planner, Scenario Expander, Reflection, Execution and Report
  agents; the bundled GNPy implementation; OpenAI request/response tracing; and
  automated tests.
- `revision_experiments/E3_llm_baseline/`: deterministic coverage baseline and
  audit of the published two-iteration LLM output.
- `revision_experiments/E4_gnpy_audit/`: published scenarios, the complete
  deterministic configuration tables, archived artifacts and GNPy audit
  outputs used by E3/E4.
- `revision_experiments/E5_recovered_gnpy_replay/`: recovered orchestration
  snapshot, NDFF topology/equipment inputs, 96-case replay inputs and results.
- `revision_experiments/E6_llm_trace_reproduction/`: portable launcher and
  instructions for a new OpenAI run that records exact API payloads,
  responses, retries, timing, model identifiers and token usage.
- `revision_documents/`: concise experiment interpretation used in the paper
  revision.

## Reproduce a traced OpenAI run

Use Python 3.12. From `llm_pipeline/`, install the project and OpenAI SDK:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . openai
Copy-Item .env.example .env
```

Put `OPENAI_API_KEY` in the local `.env` file. The file is ignored by Git and
must never be committed. Then run from the repository root:

```powershell
.\revision_experiments\E6_llm_trace_reproduction\run_local.ps1
```

New traces are written below
`revision_experiments/E6_llm_trace_reproduction/runs/<run_id>/llm_trace/`.
They are new reproduction evidence, not a reconstruction of the unavailable
historical API trace. See `llm_pipeline/LLM_REPRODUCTION.md` for the output
schema.

## Important evidence boundary

The historical run preserved its generated scenario/report artifacts and the
later-recovered prompt implementation, but it did not preserve complete
per-call API request/response logs, a dated model snapshot, retries, latency,
token usage or cost. Those fields are therefore reported as unavailable for the
historical run. The E6 instrumentation records them for future reruns without
writing API keys or proxy credentials.

The full optical and wireless telemetry release is intentionally outside this
focused repository. Only inputs and outputs required to inspect the LLM/GNPy
experiments are included here.

## Source snapshots

- Enhanced pipeline: `WanxinZhao/ECOC2026_Code` commit `57b04f1`.
- Paper/revision material: `WanxinZhao/JOCN-Dataset-WanxinCopy-` commit
  `c7ac4c9`, plus the matching local experiment archive.
- Detailed file hashes and tool versions are retained in each experiment's
  `provenance.json` and manifest files.

