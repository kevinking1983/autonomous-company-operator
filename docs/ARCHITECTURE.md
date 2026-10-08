# Architecture

How the Autonomous Company Operator is built, and why each piece exists. The
[README](../README.md) says how to run it; [DECISIONS.md](DECISIONS.md) records
the choices made along the way.

## The shape of it

```
   ticket / supervisor request                         people
            │                                    (dashboard, CLI)
            ▼                                           │
   ┌──────────────────┐     ┌──────────────────────┐    │ approve · reject · answer
   │ Operator API     │────►│ Task queue (SQLite)  │    │
   │ FastAPI, SSE     │     │ priorities, leases,  │    ▼
   └──────────────────┘     │ re-checks of paused  │  ┌───────────────────────┐
            ▲               │ tasks, sub-tasks     │  │ Human channel         │
            │               └──────────┬───────────┘  │ durable requests:     │
     dashboard (React)                 │ claim        │ approvals, questions, │
                                       ▼              │ waits for customers   │
  ┌───────────────────────── Operator runtime (one per task) ─────────────────────────┐
  │                                                                                    │
  │   UNDERSTAND ──► PLAN ──► EXECUTE ──► OBSERVE ──► ADAPT ──┐       VERIFY ──► COMPLETE │
  │   reads, then a      steps with     one decision   classify │        independent      │
  │   task contract      expected       at a time      failure, │        session, no      │
  │   (success           results                       retry /  │        grants; checks   │
  │   criteria)              ▲                         replan / │        the contract     │
  │                          └──────── replan ─────────escalate─┘        against systems   │
  │                                                                                    │
  │   checkpoint (state.json) after every step · budgets · loop check · hand-over      │
  └───────┬──────────────────┬────────────────────┬───────────────────┬────────────────┘
          ▼                  ▼                    ▼                   ▼
   Company Pack        Tool registry        Policy engine         Memory (SQLite)
   SOPs, policy,       12 typed tools,      decisions, grants,    learned facts,
   permissions,        failure taxonomy     request guard         episodes
   systems, records         │                    ▲
                            ▼                    │ every non-GET request
                     Browser (Playwright) ───────┘
                            │
                            ▼
              QuickBite sandbox: Support Desk · Ops Admin · Payments
              (separate apps, own logins, fault injection, control API)

   Everything above writes to the run's append-only audit log (events.jsonl):
   the dashboard, run reports, replay and the integrity checks are built from it.
```

## Components

| Part | Code | What it does |
|---|---|---|
| Company Pack | `company_packs/quickbite/`, `company_operator/company_pack/` | Everything company-specific, as data: role, systems and logins, SOPs (Markdown with success criteria), compensation and approval policy (YAML rules), permissions (actions with an effect class and the HTTP requests that perform them), record types and where to read them. |
| Policy engine | `company_operator/policy/engine.py` | Evaluates a declared action against the approval rules (allow / needs approval / deny), issues **grants** (single-use for money and irreversible actions), and **guards every non-GET request** the browser makes: a request goes through only if it matches a live grant whose facts agree with the submitted form. |
| Tools | `company_operator/tools/` | 12 tools: `browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_select`, `browser_screenshot`, `view_attachment`, `check_policy`, `request_approval`, `ask_human`, `wait_for_reply`, `delegate_tasks`. Every result is classified: transient, uncertain, rejected, not found, policy, needs approval, invalid input. Reads retry automatically; writes never retry blindly. |
| Browser | `company_operator/tools/browser.py`, `snapshot.js` | Playwright Chromium. Pages are read as an accessibility-style snapshot with element refs (`[e12]`); screenshots are kept as evidence. Signs in per system (retrying a failing sign-in page), stays inside the company's systems, warns when a click would discard unsaved edits in another form, and says which button saves a form after a fill. |
| Runtime | `company_operator/runtime/` | The state machine. Writes a **task contract** before acting, plans steps with expected results, takes one decision at a time, applies **Adapt rules** per failure kind, enforces budgets and a loop check, hands over before escalating, and checkpoints after every step so a run survives restarts and long waits. |
| Brain | `company_operator/planning/`, `company_operator/llm/` | Prompts built from the Company Pack, learned facts, similar past episodes and the pages seen so far. Provider-neutral clients (Gemini REST with function calling; any OpenAI-compatible API) behind a model chain with retries, fallback to the next model, and cooldowns on rate limits. |
| Verifier | `company_operator/verify/` | Independent of the executor: its own browser session, its own policy engine with **no grants** (it cannot change anything), its own prompt. Re-reads every record the run touched, judges each contract criterion with evidence, and adds checks in code (no money movement happened twice). Writes `report.json` and `report.md`. |
| Human channel | `company_operator/tools/human.py` | Durable requests in SQLite: approvals (a supervisor may approve a lower amount, never a higher one), clarifications, and waits for a customer's reply on a ticket. A paused run resumes in any process once answered. |
| Memory | `company_operator/memory/store.py` | Learned company facts (from rejected approvals, or added by people) and episodes (one per finished run), fed to later runs. |
| Queue | `company_operator/queue/` | Priorities, de-duplication by ticket, leases with heartbeats (a crashed worker's task is picked up again), periodic re-checks of paused tasks, and sub-tasks for supervisor requests. |
| API and dashboard | `company_operator/api/`, `dashboard/` | FastAPI under `/api` with Server-Sent Events for live runs; React dashboard served at `/`. |
| Evals | `company_operator/evals/` | Runs tickets under fault profiles and scores them against the sandbox's own records. |
| Sandbox | `sandbox/quickbite/` | Three server-rendered apps with seeded data for every scenario, scripted customers, a fault switchboard, and a control API that only the harness and the dashboard's switchboard use. |

## One run, end to end

Ticket TKT-1001: *"Hi, my Coke wasn't in the bag."*

1. **Understand.** The brain reads the ticket, the order, the payment and the
   customer's history (at most 8 reads), picks the missing-item SOP and writes
   the contract: *one ₹60 refund on PAY-90031; the customer is told; the ticket
   is resolved with an internal note.*
2. **Plan.** Steps with expected results: refund, reply, note and resolve.
3. **Execute, Observe.** One decision at a time. To refund, the brain fills
   the form and clicks *Confirm refund* while **declaring**
   `payments.refund {payment_id, amount: 60, reason: missing_item}`. The policy
   engine allows it (under ₹500, not a repeat claimant), issues a single-use
   grant, and the request guard checks the submitted form matches it (₹60, not
   ₹600) before the request leaves the browser.
4. **Adapt.** If the payments system answers 504 after the money moved, the
   result is *uncertain*: the runtime forces a fresh look at the payment before
   anything else, so the operator finds the refund and does not pay again.
5. **Verify.** A fresh read-only session reopens the order, payment, refund and
   ticket pages. The verifier passes each criterion only with evidence from
   those pages, and the code check confirms no money movement happened twice.
   If a criterion fails, the runtime replans and verifies again (two
   verification rounds at most); if it still fails, the operator hands the
   ticket over with a note instead of claiming success.
6. **Complete.** Summary for the supervisor, report, episode saved to memory.

## Safety, in layers

1. **Permissions.** The Company Pack lists what the role may do. Anything not
   listed, or marked forbidden, is denied.
2. **Approval rules.** Above ₹500, full-order refunds, repeat claimants,
   payment reversals and large batches wait for a person.
3. **The request guard.** The browser cannot send a write the operator did not
   declare, or declare one thing and submit another. Grants for money are
   single-use.
4. **Retry discipline.** Uncertain writes are never retried without
   re-reading the record.
5. **Independent verification** with no power to change anything, plus the
   "never twice" check in code.
6. **Hand-over.** Every stop leaves the ticket on hold with a note of what was
   checked and changed.

Prompt injection: customer messages are untrusted text inside the prompt. The
layers above bound what a misled model can do: it cannot exceed its
permissions, skip an approval, or move money it did not declare. Within its
permissions, a model can still be misled (see the README's known limitations).

## Data on disk

| Where | What |
|---|---|
| `data/runs/<run id>/state.json` | The run's checkpoint (contract, plan, observations, memory) |
| `data/runs/<run id>/events.jsonl` | The append-only audit log; a resumed run carries on the same log |
| `data/runs/<run id>/evidence/` | Screenshots (approvals, verification), attachments, the live browser frame |
| `data/runs/<run id>/report.{json,md}` | The run report |
| `data/operator.db` | Human requests, learned facts, episodes, the task queue |
| `data/evals/<eval id>/` | Eval plan, results, scorecard and every eval run's files |
