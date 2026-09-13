# E6 preflight validation

Date: 2026-09-12 (Asia/Shanghai)

- Python environment: isolated Python 3.12 virtual environment at
  `JOCN-Dataset/.runtime/llm-trace-reproduction`.
- Dependencies: the editable GNPy project, OpenAI SDK, HTTPX, and GNPy numerical
  dependencies installed successfully.
- Audited inputs loaded successfully: 26 topology elements and 7 equipment
  sections.
- Automated tests: 3/3 passed.
  - complete request/response, token and cost persistence;
  - separate failed/successful retry records;
  - strict mode prevents silent rule fallback.
- No-key preflight: exited at the first Planner OpenAI call as designed.
- No-key trace: one complete request file, one failed call record, one error
  record, `trace_status=failed`, and `openai_api_key_configured=false`.
- Network requests billed by the no-key preflight: none; the missing-key check
  stopped execution before client creation.

This preflight is not an experimental LLM result. Formal outputs will be stored
under `runs/<run_id>/` after a valid local API key is configured.

## Follow-up run

On 2026-09-13, a new OpenAI run was attempted using an API key loaded at runtime
from `/home/ubuntu/Desktop/Decomposition-code/.env`. Two API calls succeeded and
their full request/response traces were saved, but GNPy execution failed and the
workflow was manually interrupted. This does not change the status of the
2026-09-12 no-key preflight and does not recover historical API records. See
[`09_E6_20260913_new_run_trace_report.md`](../../revision_documents/09_E6_20260913_new_run_trace_report.md)
for exact counts and limitations.
