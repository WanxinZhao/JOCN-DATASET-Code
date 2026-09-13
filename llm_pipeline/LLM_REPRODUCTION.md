# OpenAI trace reproduction

This run creates new reproduction evidence. It does not recreate an unavailable
historical API trace.

## Local setup

1. Create and activate a Python 3.12 virtual environment.
2. Install this project and the OpenAI SDK: `python -m pip install -e . openai`.
3. Copy `.env.example` to `.env`, set `OPENAI_API_KEY`, and keep
   `PHYSICAL_LAYER_DB_BACKEND=file`.
4. Set `TOPOLOGY_PATH` and `EQUIPMENT_PATH` to the audited input files. Relative
   paths are resolved from this project; absolute paths are accepted.

Keep `LLM_STRICT_MODE=1` for the reproduction. This makes an OpenAI failure stop
the run instead of silently substituting a deterministic fallback.

The recovered historical source requested the moving alias `gpt-4o-mini`. The
reproduction configuration pins the official `gpt-4o-mini-2024-07-18` snapshot
so that the new run has a precise model identifier. The manifest records both
the requested identifier and the model returned by the API.

Run non-interactively:

```powershell
python main.py --request "Generate a raw QoT dataset for all NDFF paths starting from Bristol using 8 Voyager transmitters only. Use 8 channel slots and enumerate all binary on/off channel patterns across the 8 slots. Simulate only QPSK and 16QAM."
```

Each run writes `data/runs/<run_id>/llm_trace/` containing:

- `run_manifest.json`: request, runtime, Git state, source/input hashes and settings;
- `requests/*.json`: the complete payload persisted before every API attempt;
- `responses/*.json`: the corresponding complete SDK response payloads;
- `calls.jsonl` and `call_summary.csv`: timing, retries, response IDs, model,
  system fingerprint, token usage, failures and optional cost estimates;
- `errors.jsonl`: failed attempts only.

API keys and proxy credentials are not written to the trace. Cost is calculated
only when the `.env` file contains a dated pricing snapshot and its source.
