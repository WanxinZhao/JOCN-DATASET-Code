# E3: Deterministic baseline and public-evidence audit

## Completed

- Generated the complete 6,144-configuration ground-truth set with a deterministic enumerator.
- Compared the published multi-agent scenario set against the deterministic set.
- Measured missing, extra and duplicate configurations.
- Audited the two published iterations and their launch powers.

## Recovered implementation evidence

The later-recovered local source contains the Planner, Scenario Expander and Reflection prompts and JSON constraints. Its default OpenAI path uses `gpt-4o-mini`, temperature 0.2, a 90-second timeout and at most three attempts. A two-iteration refine path contains up to six LLM call sites. The source snapshot and hashes are retained under `../E5_recovered_gnpy_replay/source/`.

The archived run still lacks its environment variables, date-stamped model snapshot, per-call requests/responses, actual calls/retries, token usage, latency, cost, failures and human-intervention trace. Therefore the source default is reported separately from the unverified actual runtime, and those run-level fields remain `not_reported`. Single-LLM, no-reflection and repeated full multi-agent trials cannot be reconstructed without fabricating conditions.

## Published reflection evidence

The published report contains one two-iteration run. Iteration 1 executes 2,048 cases at -5.5 dBm. Iteration 2 executes 6,144 cases at -5.5, -5.0 and -4.5 dBm, which repeats all 2,048 first-iteration configurations. This is execution history, not 8,192 unique dataset configurations.
