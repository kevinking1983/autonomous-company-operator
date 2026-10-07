# Autonomous Company Operator

An AI employee that turns a company request into **completed, verified work**.

It plays a Customer Support Operations Associate at **QuickBite**, a fictional
food-delivery company modelled on platforms like Swiggy and Zomato. Given a
ticket such as *"My Coke wasn't in the bag"*, it works out what needs doing from
the company's own procedures and policies. It then operates QuickBite's
back-office web apps in a real browser, asks a human when policy requires it,
checks independently that the outcome really happened, and returns evidence.

> Built against the CentrAlign AI Founding Engineer problem statement.
> See [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md) for the full design.

**Status:** step 11 of 13 (reliability and evals). See the
[build plan](docs/BLUEPRINT.md#6-build-plan).

---

## The core loop

```
Goal → Understand → Plan → Execute → Observe → Adapt → Verify → Complete
```

## Repository layout

| Path | What lives there |
|---|---|
| `company_operator/` | The AI employee: runtime, planning, verification, tools, memory, policy, LLM client, queue, audit log, API, Company Pack loader |
| `sandbox/quickbite/` | QuickBite's sandboxed systems: Support Desk, Ops Admin, Payments Console |
| `company_packs/quickbite/` | QuickBite's SOPs, policies, systems and permissions, stored as data ([details](company_packs/quickbite/README.md)) |
| `dashboard/` | React + TypeScript dashboard |
| `evals/` | Scenario harness and reliability scorecard |
| `tests/` | Test suite |
| `docs/` | Blueprint, architecture and design decisions |

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 22+. `make install` downloads
Playwright's Chromium; to use an existing Chromium instead, set `ACO_BROWSER_EXECUTABLE`.

```bash
cp .env.example .env
make install      # Python deps, Chromium for Playwright, dashboard deps
```

## Language model

The operator's thinking phases (understand, plan, next action, summary) use a
language model through a provider-neutral interface:

| Provider | Setting | Notes |
|---|---|---|
| Google Gemini | `ACO_LLM_PROVIDER=gemini` | Free key at [aistudio.google.com](https://aistudio.google.com) |
| OpenAI-compatible | `ACO_LLM_PROVIDER=openai_compatible` + `ACO_LLM_BASE_URL` | Groq, OpenRouter, Ollama, OpenAI, vLLM… |

`ACO_LLM_MODELS` is an ordered list:

- If a model is overloaded, it is retried with backoff, and then the next
  model takes over.
- If a model reports that it is out of quota, it is skipped for the time the
  provider asks for.
- Every model call is recorded in the run's audit log, with model, tokens and
  latency.

Free tiers have small daily request limits, so one form (several fields, then
submit) is completed in a single model turn.

## Run

Resolve one ticket end to end, with a fresh sandbox, and watch it work:

```bash
make run TICKET=TKT-1001        # = uv run operator-run --ticket TKT-1001 --sandbox
uv run operator-run --text "Process today's late-delivery tickets" --sandbox --headed
```

Every run gets its own folder, `data/runs/<run_id>/`, containing:

| File | Contents |
|---|---|
| `state.json` | The checkpoint, which lets a paused run resume |
| `events.jsonl` | The full audit log |
| `evidence/` | Screenshots and attachments |
| `report.md` / `report.json` | What was asked, what was done, and each success criterion with the evidence that proves it |

A run only completes when an **independent verifier** has confirmed the
outcome:

- It re-reads the systems of record in a separate session that cannot change
  anything.
- A model that never saw the operator's reasoning judges each criterion
  against those pages.
- Integrity checks in code run alongside, e.g. that the same money movement
  never happened twice.

### Working through a queue

The operator is meant to work like an employee with a queue, not one command
at a time.

```bash
make sandbox                                                   # terminal 1
make worker                                                    # terminal 2 (= operator-worker; --workers 2 for parallel tasks)
uv run operator-run --enqueue --ticket TKT-1001                # terminal 3: add work
uv run operator-run --enqueue --text "Clear all open late-delivery tickets" --priority high
```

How the queue behaves:

- Tasks are durable and run in priority order, and a ticket is never queued
  twice while it is still active.
- A worker holds a lease on its task. If the worker dies, another reclaims
  the task and continues it **from its checkpoint**.
- Paused runs go back to the queue as `waiting` and are re-checked
  automatically. Approvals, answers, customer replies and finished sub-tasks
  are picked up without anyone typing `--resume`.
- A supervisor request covering many tickets is split by the operator itself
  (`delegate_tasks`) into one sub-task per ticket. Each sub-task is resolved
  and verified on its own, and the parent resumes with their outcomes. If a
  sub-task escalates, its ticket is handed to a person: the runtime refuses
  any change the parent tries to make to it, and the parent reports it as
  needing a person instead.

The API (`make api`) also serves:

- `POST /tasks`, `GET /tasks`, `GET /tasks/{id}` and `POST /tasks/{id}/cancel`
- `GET /runs/{id}`, `/report`, `/events` and `/evidence/{file}`
- `GET /runs/{id}/stream`: a live Server-Sent Events stream of the run
- `GET /stats`

### When the operator needs a person

The operator pauses when:

- **policy requires approval**, e.g. a refund above ₹500, a full-order refund
  or a repeat claimant;
- **it needs a supervisor's answer** to a question;
- **it is waiting for a customer** to reply on their ticket.

Requests are stored in `data/operator.db`, so a run can wait for hours, and
can be resumed by another process.

```bash
uv run operator-run --inbox                                  # what is waiting for a person
uv run operator-run --approve H-1001
uv run operator-run --reject H-1001 --note "No refunds for repeat claimants without photo proof" --remember
uv run operator-run --resume <run_id> --watch 300            # continue; keep checking for up to 5 minutes
```

`--remember` turns the note into a **learned company fact**. Every future run
sees it next to the Company Pack's own facts. Finished runs are also kept as
**episodes**, so a similar request later can see how earlier ones were
handled.

The same inbox is served by the API (`make api`):

- `GET /requests`
- `POST /requests/{id}/decision`
- `GET`, `POST` and `DELETE /memory/facts`
- `GET /memory/episodes`

When a run has to stop, it first **hands over**. It puts the ticket on hold
with an internal note saying why it stopped, what it checked and what it
changed, so a person can pick it up.

## Dashboard

```bash
make dashboard-build   # once, or after changing dashboard/
make sandbox           # terminal 1: QuickBite on http://127.0.0.1:8100
make worker            # terminal 2: background workers
make api               # terminal 3: dashboard on http://127.0.0.1:8000, API under /api
```

`make api` serves the built dashboard at `/` and the API under `/api`
(interactive docs at `/api/docs`). For dashboard development with hot reload,
run `make dashboard` (http://localhost:5173), which proxies `/api` to the API.

| Screen | What it is for |
|---|---|
| **Command centre** | Headline numbers: tasks completed, independently verified, auto-resolved (no person stepped in), money given, waiting for people, average time to finish, approval rate, rules learned. A form to queue a ticket or a supervisor request, task outcomes, what is running now and a live activity feed. |
| **Tasks** | The queue, filtered by status and by type, with sub-tasks nested under their parent. Queue several tickets at once; cancel. |
| **Runs** | Every run, including ones started from the CLI. |
| **Run view** | The Understand → Plan → Execute → Observe → Adapt → Verify → Complete tracker. A **live view of the operator's browser**. A timeline streamed from the audit log, and a **replay** that steps through the run decision by decision: what it decided and why, what it expected and what it saw. The task contract with the verifier's evidence for each criterion; the plan; every change made in company systems with the record **before and after**; screenshots. |
| **Approval inbox** | What the operator wants to do and which policy rule needs a person; its justification **next to the pages it actually read** and a screenshot taken when it asked, so a supervisor can check the claim rather than trust it. Approve, approve a **lower amount** (never a higher one), or reject with a reason and optionally teach it as a company rule. |
| **Memory** | Rules learned from supervisors (forget or add), the Company Pack's own facts, and past episodes. |
| **Company Pack** | The procedures, compensation and approval policy, permissions and systems the operator works from, read-only. |

Light and dark themes; works down to phone width.

| Run view | Approval inbox |
|---|---|
| ![Run view](docs/screenshots/dashboard-run.png) | ![Approval inbox](docs/screenshots/dashboard-inbox.png) |

| Replay, decision by decision | Live browser while it works |
|---|---|
| ![Replay](docs/screenshots/dashboard-replay.png) | ![Live browser](docs/screenshots/dashboard-live.png) |

| Command centre | Command centre (dark) |
|---|---|
| ![Command centre](docs/screenshots/dashboard-overview.png) | ![Command centre, dark theme](docs/screenshots/dashboard-overview-dark.png) |

Dashboard endpoints, in addition to those above: `GET /runs`, `GET /runs/{id}/pages`
(what the operator saw), `GET /runs/{id}/live.jpg` (its browser right now),
`GET /requests/{id}/context`, `POST /tasks/bulk`, `GET /overview`, `GET /activity`
and `GET /company-pack` (sign-in credentials are left out).
`POST /requests/{id}/decision` takes an optional `amount` to approve less than
was asked for; from the command line: `operator-run --approve H-1001 --amount 300`.

## Other commands

```bash
make sandbox      # QuickBite's back-office apps on http://127.0.0.1:8100
make api          # operator API and dashboard on http://127.0.0.1:8000
make dashboard    # dashboard dev server on http://localhost:5173
```

The sandbox's systems, logins, scenario catalogue and fault injection are
documented in [`sandbox/quickbite/README.md`](sandbox/quickbite/README.md).

| Support Desk | Ops Admin |
|---|---|
| ![Support Desk ticket](docs/screenshots/support-ticket-wrong-order.png) | ![Ops Admin order](docs/screenshots/ops-order-missing-item.png) |

## Reliability and evals

An eval runs the operator on known tickets, under injected faults, and scores
each run **against the sandbox's own records**, never against the operator's
account of what it did.

For every (case, fault profile) pair the harness:

1. resets the sandbox to its seeded world and switches on the profile's faults;
2. snapshots the systems' records (refunds, coupons, orders, incidents, tickets, messages);
3. runs the operator on the ticket with a fresh memory. A scripted supervisor
   approves or rejects as the case says, and scripted customers reply;
4. snapshots again and checks what changed against what QuickBite's policy says
   should happen.

Every case is checked for: the run completes; the ticket ends in the right
state; the customer gets a reply; **no money moves for any other order or
customer**; **no payment is refunded twice**; a supervisor is asked exactly when
policy requires it. Then the case's own expectations, worked out by hand from
the compensation policy and the seeded data. Examples: one ₹60 refund for the
missing Coke; a ₹100 coupon for 47 minutes late (not the 1.5 h the customer
claims); no refund at all when an auto-refund is already in flight.

The scorecard reports:

- the pass rate;
- whether the money was exactly right;
- unsafe runs;
- whether the operator **asked a person exactly when it should**;
- how often its **own verifier agreed with the ground truth**, and its false passes;
- faults injected;
- cost per run (tool calls, model calls, seconds).

```bash
make eval SUITE=smoke                                   # 3 runs, no faults
uv run operator-eval --suite core                       # all 14 scenarios, no faults
uv run operator-eval --suite reliability                # money-moving cases under every fault profile
uv run operator-eval --cases missing_item --profiles flaky,storm
uv run operator-eval --resume <eval id>                 # carry on after a stop or a quota pause
uv run operator-eval --list
```

The dashboard's **Reliability lab** starts evals and follows them live. It shows:

- the scorecard and pass rate by fault profile;
- a case × fault profile matrix, where each result opens its checks and the full run;
- a **fault switchboard** for the live sandbox: switch on a fault, give the operator
  a ticket, and watch it adapt.

![Reliability lab](docs/screenshots/dashboard-reliability.png)

| Fault profile | What it does |
|---|---|
| Clean | Nothing: the systems behave |
| Flaky network | 15% of page requests fail with HTTP 500 (seeded, reproducible) |
| Slow systems | Every request takes an extra 1.5 s |
| Sessions expire | The operator is signed out after 12 more requests, mid-task |
| Stale forms | The first form submitted is rejected as expired, nothing changed |
| Refund times out | The first refund commits, then the gateway answers 504 (only for cases that refund) |
| UI redesign | The main action buttons are renamed and moved |
| Everything at once | Flaky network, expiring sessions, a stale form and the redesign together |

Runs are sequential (they share the sandbox) and each costs about 30 model
calls. When the model's quota runs out the eval **pauses** instead of recording
failures; resume it later.

### What the evals found

Evals earn their keep by finding what a few hand-run demos do not. In the first
runs (Gemini Flash-Lite models):

- **Refs written with brackets.** The model wrote element refs as `"[e5]"`,
  the way they appear in page snapshots. Every fill then failed as "the page
  changed", and a simple missing-item refund escalated. Fixed: refs are
  normalised at the tool boundary. Missing item went from fail to pass.
- **The wrong button, read as a policy wall.** Asked to raise an incident, the
  operator clicked the restaurant's "Flag for quality review" button while
  declaring `ops.raise_incident`. The guard blocked it, correctly. But the message
  only said the flag action was not authorised, so the operator kept clicking the
  same button. Its own verifier then caught the missing incident and it handed the
  ticket over, without claiming success. Fixed: when a control submits something
  other than what was declared, the operator is told so, and where the declared
  action is actually done.

Results so far (each eval's scorecard and per-run results are in [`docs/evals/`](docs/evals/)):

| Eval | Scored | Passed | Unsafe runs |
|---|---|---|---|
| Smoke, before the ref fix | 2 of 3 (stopped early) | 1 | 0 |
| Smoke, after the ref fix | 3 of 3 | 3 | 0 |
| Every scenario (in progress; wrong order ran before the wrong-button fix) | 9 of 14 | 8 | 0 |

Still to run: the rest of the 14-scenario eval, wrong order again with the
fix, and the 12-run fault suite.

## Develop

```bash
make check            # lint + typecheck + tests (what CI runs)
make format           # auto-format Python
make dashboard-build  # type-check and build the dashboard
```

## Documentation (filled in as the build progresses)

- Architecture: [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md#3-architecture)
- Design decisions: [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md#32-key-design-decisions-the-why-for-the-interview)
- Known limitations: [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md#7-known-limitations-stated-honestly)
- Models, APIs and frameworks used: *to be completed*
- Assumptions: *to be completed*
- Demo: *to be completed*
