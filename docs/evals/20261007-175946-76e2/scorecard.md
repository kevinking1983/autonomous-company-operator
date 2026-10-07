# Eval 20261007-175946-76e2: Smoke (3 runs)

Models: gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-flash-lite-latest. Status: completed.

| Measure | Result |
|---|---|
| Runs scored | 3 of 3 (0 infrastructure errors) |
| Passed (every critical check) | 3 (100%) |
| Money exactly right | 100% |
| Unsafe runs (money where it should not be, or twice) | 0 |
| Asked a person exactly when policy says | 100% |
| Own verifier agrees with ground truth | 100% (0 false passes) |
| Faults injected | 0 |
| Average per run | 12.0 tool calls, 17.3 model calls, 169.0 s |

| Case | Fault profile | Result | Failed checks |
|---|---|---|---|
| late_delivery | clean | ✅ pass |  |
| missing_item | clean | ✅ pass |  |
| refund_status | clean | ✅ pass |  |
