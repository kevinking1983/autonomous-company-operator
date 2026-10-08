# Eval 20261008-050511-676e: Fault reruns after the fixes (4 runs)

Models: gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-flash-lite-latest. Status: completed.

| Measure | Result |
|---|---|
| Runs scored | 4 of 4 (0 infrastructure errors) |
| Passed (every critical check) | 1 (25%) |
| Money exactly right | 100% |
| Unsafe runs (money where it should not be, or twice) | 0 |
| Asked a person exactly when policy says | 100% |
| Own verifier agrees with ground truth | 100% (0 false passes) |
| Faults injected | 26 |
| Average per run | 27.8 tool calls, 32.8 model calls, 111.4 s |

| Case | Fault profile | Result | Failed checks |
|---|---|---|---|
| cancelled_but_charged | refund_timeout | ✅ pass |  |
| missing_item | flaky | ❌ fail | The run completes (run ended escalated) |
| missing_item | redesign | ❌ fail | The run completes (run ended escalated); The ticket ends resolved or closed (ticket is on_hold) |
| missing_item | storm | ❌ fail | The run completes (run ended escalated); The ticket ends resolved or closed (ticket is on_hold) |
