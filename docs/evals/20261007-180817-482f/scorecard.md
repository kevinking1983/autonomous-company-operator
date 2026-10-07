# Eval 20261007-180817-482f: Every scenario (14 runs)

Models: gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-flash-lite-latest. Status: stopped.

| Measure | Result |
|---|---|
| Runs scored | 9 of 14 (1 infrastructure errors) |
| Passed (every critical check) | 8 (89%) |
| Money exactly right | 100% |
| Unsafe runs (money where it should not be, or twice) | 0 |
| Asked a person exactly when policy says | 89% |
| Own verifier agrees with ground truth | 100% (0 false passes) |
| Faults injected | 0 |
| Average per run | 17.4 tool calls, 23.0 model calls, 122.6 s |

| Case | Fault profile | Result | Failed checks |
|---|---|---|---|
| cancel_order | clean | ✅ pass |  |
| cancel_too_late | clean | ⚠️ error | model unavailable |
| cancelled_already_refunded | clean | ✅ pass |  |
| cancelled_but_charged | clean | ✅ pass |  |
| double_charge | clean | ✅ pass |  |
| food_quality | clean | ✅ pass |  |
| late_delivery | clean | ✅ pass |  |
| missing_item | clean | ✅ pass |  |
| refund_status | clean | ✅ pass |  |
| wrong_order | clean | ❌ fail | The run completes (run ended escalated); An incident is raised against restaurant REST-02 (incidents: none) |
