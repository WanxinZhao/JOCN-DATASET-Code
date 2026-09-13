# E6: OpenAI trace reproduction

## Purpose

This experiment reruns the recovered multi-agent workflow while preserving the
runtime-rendered API requests and complete OpenAI response objects. It is new
reproduction evidence and must not be described as the unavailable original
historical API trace.

## Reproduction configuration

- Provider: OpenAI (author-confirmed historical provider).
- Source implementation: [`WanxinZhao/ECOC2026_Code@57b04f1`](https://github.com/WanxinZhao/ECOC2026_Code/commit/57b04f1).
- Model requested for the new run: `gpt-4o-mini-2024-07-18` (fixed snapshot).
- Historical source model string: `gpt-4o-mini` (moving alias).
- Temperature: `0.2`.
- Timeout: `90 s`.
- Maximum attempts per provider: `3`.
- Strict LLM mode: enabled; an API failure cannot be hidden by a rule fallback.
- Topology: the audited `E5_recovered_gnpy_replay/source/NDFF_Testbed.json`.
- Equipment: the audited schema-fixed
  `E5_recovered_gnpy_replay/source/eqpt_config_NDFF_schema_fixed.json`.
- Pricing snapshot date: `2026-09-12`.
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

## Current status

The code, isolated Python environment, input-path check, strict-failure preflight,
and automated logging tests are complete. The real OpenAI rerun is pending because
`OPENAI_API_KEY` is not currently configured on this computer. After the key is
set locally, run `run_local.ps1`; no key should be pasted into Git or chat.
