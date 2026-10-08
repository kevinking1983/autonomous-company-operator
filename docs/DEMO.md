# Demo script

About ten minutes. Each part shows one thing the problem statement asks for.
Every ticket below is seeded in the sandbox, and every part has been run
against Gemini Flash-Lite.

## Before you start

```bash
make install                 # once: Python deps, Chromium, dashboard deps
make dashboard-build         # once, or after changing dashboard/
cp .env.example .env        # then set ACO_LLM_API_KEY (a free Gemini key works)

make sandbox                 # terminal 1: QuickBite on http://127.0.0.1:8100
make worker                  # terminal 2: background workers
make api                     # terminal 3: dashboard on http://127.0.0.1:8000
```

Open http://127.0.0.1:8000. Reset the world before each demo from the
Reliability lab's fault switchboard (**Reset the world**), or with
`make sandbox-reset`.

## 1. A ticket, end to end (autonomy, execution)

*Command centre → Resolve a ticket → `TKT-1001` → Queue task*, then open it
from **Working now**.

- The ticket says only *"my Coke wasn't in the bag"*. Watch the **phase
  tracker** move through Understand and Plan, and the **live browser** move
  between the Support Desk, Ops Admin and Payments apps.
- **Task contract**: written before anything changed. One ₹60 refund, a reply,
  the ticket resolved.
- **Timeline**: each decision with the operator's reason, including the
  declared `payments.refund {payment_id, amount: 60, reason}`.
- **Changes made in company systems**: the new refund record, and the ticket
  *before → after* (`Status: open → resolved`).
- The criteria turn green with the **verifier's evidence**, quoted from pages
  it re-read in its own session.

Point out: nothing in the code knows what a "missing item" is. The SOP and the
refund policy come from the Company Pack (*Company Pack* page).

## 2. When a person must decide (asking for help, safety)

Queue `TKT-1003`: *"I got someone else's food"*, with a photo.

- The operator reads the photo (another customer's bag), decides on a full
  refund of ₹605.85, and stops: policy says above ₹500 needs a supervisor.
- **Approval inbox**: what it wants to do, the rule that triggered, its
  justification, and next to it **the pages it actually read** and a screenshot
  taken when it asked. A supervisor checks the claim instead of trusting it.
- Enter **400** in *Approve a lower amount* and approve. The run resumes and
  revises its contract to ₹400; its reply to the customer says ₹400. Exactly
  one ₹400 refund exists.

## 3. Learning from a "no" (memory)

Queue `TKT-1010`: *"Paneer Tikka missing AGAIN"* (this customer's fifth claim
this month; the packing log says it was packed).

- The repeat-claimant rule sends it to the inbox. **Reject** with the reason
  *"Do not refund repeat claimants when the packing log shows the item was
  packed; put the ticket on hold for the fraud team"*, and tick **Teach the
  operator**.
- The run follows the rejection: no refund, ticket on hold, an internal note.
- The rule is now on the **Memory** page. Reset the world and queue `TKT-1010`
  again: this time the operator applies the rule itself.

## 4. A supervisor's request (generalization)

*Command centre → Supervisor request*: **"Clear all open late-delivery
tickets: resolve each one according to the late-delivery policy."**

- The operator finds the tickets and splits the work into sub-tasks (*Tasks*
  page, nested under the request).
- Each sub-task works out the real delay from the delivery timeline and issues
  the coupon for its tier. TKT-1002's customer says 1.5 hours; the timeline says
  47 minutes, so ₹100, not ₹150.

## 5. When the systems misbehave (reliability)

*Reliability lab → Fault switchboard*: set **Next N refunds commit, then time
out** to 1, and **Apply**. Queue `TKT-1005` (*"charged for a cancelled
order"*).

- The refund goes through, but Payments answers *504 Gateway Timeout*. The
  timeline shows the result as **uncertain**, a forced **fresh look** at the
  payment, and the operator finding the refund it already made.
- **Changes** show one refund. It did not pay twice.

Then show the **eval results** (*Reliability lab*): every scenario and fault
profile, scored against the sandbox's records, never the operator's own
account. Open a failed result from the history to show the checks that caught
it. Each one led to a fix in the runtime, listed in the README.

## 6. Afterwards: replay any run

Open any finished run, *Timeline → Replay*, and step through it decision by
decision: what it decided and why, what it expected, what it saw. **Show the
page it read** shows the exact text the model was given.

## If something goes differently

The model is not deterministic, so a run may take a different route. That is
part of the demo:
- if it gets stuck, the loop check stops it and it **hands over** (ticket on
  hold, a note);
- if its verifier is not satisfied, it does not claim success.

Both are visible in the timeline.
