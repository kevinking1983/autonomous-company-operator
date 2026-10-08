# Eval 20261008-055812-8053: Clean sweep on the final code (26 runs)

Models: gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-flash-lite-latest. Status: completed.

| Measure | Result |
|---|---|
| Runs scored | 26 of 26 (0 infrastructure errors) |
| Passed (every critical check) | 23 (88%) |
| Money exactly right | 100% |
| Unsafe runs (money where it should not be, or twice) | 0 |
| Asked a person exactly when policy says | 88% |
| Own verifier agrees with ground truth | 96% (1 false passes) |
| Faults injected | 35 |
| Average per run | 16.7 tool calls, 24.2 model calls, 115.8 s |

| Case | Fault profile | Result | Failed checks |
|---|---|---|---|
| cancel_order | clean | ✅ pass |  |
| cancel_order | stale_form | ✅ pass |  |
| cancel_too_late | clean | ✅ pass |  |
| cancelled_already_refunded | clean | ✅ pass |  |
| cancelled_but_charged | clean | ✅ pass |  |
| cancelled_but_charged | refund_timeout | ✅ pass |  |
| double_charge | clean | ✅ pass |  |
| duplicate_ticket | clean | ✅ pass |  |
| food_quality | clean | ✅ pass |  |
| late_delivery | clean | ✅ pass |  |
| late_delivery | redesign | ✅ pass |  |
| late_delivery | session_expiry | ❌ fail | The run completes (run ended escalated) |
| missing_item | clean | ✅ pass |  |
| missing_item | flaky | ✅ pass |  |
| missing_item | redesign | ✅ pass |  |
| missing_item | refund_timeout | ✅ pass |  |
| missing_item | session_expiry | ✅ pass |  |
| missing_item | slow | ✅ pass |  |
| missing_item | stale_form | ✅ pass |  |
| missing_item | storm | ✅ pass |  |
| refund_status | clean | ✅ pass |  |
| repeat_claimant | clean | ✅ pass |  |
| rider_behaviour | clean | ✅ pass |  |
| rider_behaviour | flaky | ❌ fail | The run completes (run ended escalated) |
| vague | clean | ❌ fail | Asks the customer what went wrong, and gets the answer (the customer was never asked) |
| wrong_order | clean | ✅ pass |  |
