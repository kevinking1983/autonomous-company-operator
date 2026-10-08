# Eval 20261008-055812-8053: Clean sweep on the final code (26 runs)

Models: gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-flash-lite-latest. Status: paused.

| Measure | Result |
|---|---|
| Runs scored | 11 of 26 (2 infrastructure errors) |
| Passed (every critical check) | 11 (100%) |
| Money exactly right | 100% |
| Unsafe runs (money where it should not be, or twice) | 0 |
| Asked a person exactly when policy says | 91% |
| Own verifier agrees with ground truth | 100% (0 false passes) |
| Faults injected | 0 |
| Average per run | 14.4 tool calls, 19.2 model calls, 98.4 s |

| Case | Fault profile | Result | Failed checks |
|---|---|---|---|
| cancel_order | clean | ✅ pass |  |
| cancel_too_late | clean | ✅ pass |  |
| cancelled_already_refunded | clean | ✅ pass |  |
| cancelled_but_charged | clean | ✅ pass |  |
| double_charge | clean | ✅ pass |  |
| duplicate_ticket | clean | ⚠️ error | model unavailable |
| food_quality | clean | ✅ pass |  |
| late_delivery | clean | ✅ pass |  |
| missing_item | clean | ✅ pass |  |
| refund_status | clean | ✅ pass |  |
| repeat_claimant | clean | ⚠️ error | model unavailable |
| rider_behaviour | clean | ✅ pass |  |
| wrong_order | clean | ✅ pass |  |
