# Autonomous Company Operator

An AI employee that turns a company request into **completed, verified work**.

It plays a Customer Support Operations Associate at **QuickBite**, a fictional
food-delivery company modelled on platforms like Swiggy and Zomato. Given a
ticket such as *"My Coke wasn't in the bag"*, it works out what needs doing from
the company's own procedures and policies. It then operates QuickBite's
back-office web apps in a real browser, asks a human when policy requires it,
checks independently that the outcome really happened, and returns evidence.

> Built against the CentrAlign AI Founding Engineer problem statement.
> **Start here:** [how it works](docs/ARCHITECTURE.md) ·
> [why it is built this way](docs/DECISIONS.md) · [a 10-minute demo](docs/DEMO.md) ·
> [eval results](#reliability-and-evals) · [limitations](#known-limitations).

![Run view](docs/screenshots/dashboard-run.png)

## What the problem statement asks, and where to see it

| Asked for | In this project |
|---|---|
| **Autonomy**: from a request to completed work, with the steps unstated | A ticket says *"my Coke wasn't in the bag"*; the operator finds the order, the packing log and the payment, picks the procedure, refunds, replies and closes the ticket. Supervisor requests are split into sub-tasks and worked in the background. |
| **Execution** in real systems | Three separate web apps with their own logins, operated through a real browser; state changes in systems the operator does not control. |
| **Reliability**: unexpected states, errors, retries, failures | Typed failures with an Adapt rule for each; reads retry; uncertain writes force a fresh look before anything else; budgets, a loop check and a hand-over. **Measured**: 14 scenarios and 7 fault profiles, scored on ground truth ([results](#results)). |
| **Verification** with evidence | A task contract written before acting; an independent verifier that can change nothing re-reads the records; evidence and screenshots per criterion; a "never pay twice" check in code. |
| **Asking for help** | Policy-driven approvals (with the evidence the operator saw), questions, waiting for customers; durable across hours and restarts; supervisors can approve a lower amount. |
| **Using company context** | The Company Pack: SOPs, policy, permissions and systems as data. Rejections become learned rules. |
| **Generalization** | No code for any ticket type: 9 ticket-type SOPs (plus general ones) and supervisor requests run on one runtime ([details](docs/DECISIONS.md#2-the-company-is-data-the-company-pack)). |
| **Engineering quality** | No agent framework; typed Python (strict mypy), 250+ tests, many against the real sandbox in a real browser, CI, an append-only audit log behind every screen. |

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
| `company_operator/evals/` | Eval harness: cases, fault profiles, scoring ([results](docs/evals/)) |
| `tests/` | Test suite |
| `docs/` | [Architecture](docs/ARCHITECTURE.md), [decisions](docs/DECISIONS.md), [demo script](docs/DEMO.md), [blueprint](docs/BLUEPRINT.md), eval results |

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 22+. `make install` downloads
Playwright's Chromium; to use an existing Chromium instead, set `ACO_BROWSER_EXECUTABLE`.

```bash
cp .env.example .env
make install      # Python deps, Chromium for Playwright, dashboard deps
```

Then set `ACO_LLM_API_KEY` in `.env`. Settings are read when a program starts,
so restart the worker and the API after changing `.env`.

### Windows (no `make`)

In PowerShell, from the project folder:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # uv, once
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"   # if `uv` is not found in this window

copy .env.example .env      # then set ACO_LLM_API_KEY in .env
uv sync
uv run playwright install chromium
cd dashboard; npm install; npm run build; cd ..
```

Run each of these in its own PowerShell window:

```powershell
uv run quickbite-sandbox    # window 1: QuickBite on http://127.0.0.1:8100
uv run operator-worker      # window 2: works through the queue
uv run company-operator     # window 3: dashboard on http://127.0.0.1:8000
```

Start all three **from the same project folder**: each copy of the project keeps its
own `data\` (queue, runs, memory) and its own `.env`, so a dashboard started from
another copy queues work this worker never sees. The worker prints how each task
ended; Ctrl+C once stops it after the current task, twice stops it now (the task
goes back to the queue).

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
  the task and continues it **from its checkpoint**. Ctrl+C once stops a
  worker after its current task; twice stops it now and hands the task back
  to the queue at once.
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

Evals earn their keep by finding what a few hand-run demos do not. Every
finding below came from an eval run (Gemini Flash-Lite models), was fixed in
the runtime (never in a case), and has a regression test.

| Found by | What went wrong | Fix |
|---|---|---|
| Smoke | The model wrote element refs as `"[e5]"`, the way they appear in snapshots; every fill failed as "the page changed", and a simple refund escalated | Refs are normalised at the tool boundary |
| Every scenario | Asked to raise an incident, the operator declared it on the restaurant's "Flag for quality review" button. The guard blocked it, correctly, but the message read like a policy wall, so it kept clicking | When a control submits something other than what was declared, the operator is told so and where the declared action is actually done |
| Faults: flaky network | The verifier's browser signed in just as the sign-in page failed with a 500; it waited 30 s for a "Username" field and crashed the run | Sign-in retries with backoff and never throws; a page the verifier cannot read counts as "not verified", not a crash |
| Faults: UI redesign, refund timeout | The model filled a form, then filled it again, never pressing the button that saves it (and once picked a ticket category after saving the form) | After a fill or select the operator is told which button saves it, and re-filling a field with the value it already holds is called out. The first exact repeat of a tool call is answered with a warning instead of being redone; repeating on still escalates |
| Faults: flaky network, storm | Retrying a failed sign-in page dropped its `?next=`, and a retried read bounced to the sign-in page returned the sign-in page as "recovered"; the verifier then judged the wrong page | Sign-in retries keep the return address; after signing in, a read always reopens the page it asked for |
| Faults: flaky network | A refund attempt failed with a 500 before anything happened; the operator re-checked, saw no refund and tried again (one refund, correct), but the "never pay twice" check counted two submissions and failed the run | The check counts confirmed submissions. An uncertain attempt (5xx) followed by another passes only if the operator re-read the records in between; a blind retry still fails |
| Clean sweep: expiring sessions | After issuing the coupon, the model marked its next step ("update the ticket and reply") done without doing either. The verifier caught it and the operator handed over | A step marked done with no action taken in it is pushed back once: do it, or quote where its result is already visible. *Fixed after the sweep, so not yet measured* |
| Clean sweep: vague ticket | *"My order was bad."* Instead of asking, the operator found the dal makhani marked NOT PACKED and refunded it, so it never heard about the burnt naan. Its own contract left out asking, so its verifier passed it: the one false pass | **Not fixed.** Verification is only as good as the contract it checks (see Known limitations) |
| Clean sweep: flaky network | Two internal-note submissions failed with a 500; after each, the operator re-read the ticket as required, then kept re-reading instead of moving on, until the loop check handed it over (with everything done) | **Not fixed:** a small-model weakness under a harsh profile (15% of all requests fail, writes included). The outcome was safe |

Through all of these the safety layers held. No run ever moved money where it
should not, or twice. When the operator could not get it right, its own verifier
refused to pass the run, and the operator handed the ticket over instead of
claiming success.

### Results

Each eval's scorecard and per-run results are in [`docs/evals/`](docs/evals/).
Model: Gemini Flash-Lite (3.5, with 3.1 and latest as fallbacks).

| Eval | Scored | Passed | Unsafe runs |
|---|---|---|---|
| Smoke, before the ref fix | 2 of 3 (stopped early) | 1 | 0 |
| Smoke, after the ref fix | 3 of 3 | 3 | 0 |
| Every scenario: 14 tickets, no faults | 14 of 14 | 13 | 0 |
| Wrong order again, after its fix | 1 of 1 | 1 | 0 |
| Faults: 12 runs under 7 fault profiles (first pass) | 10 of 12 | 8 | 0 |
| Faults: the 4 runs that did not pass, after fixes | 4 of 4 | 1 | 0 |
| Faults: the 3 still failing, after more fixes | 3 of 3 | 2 | 0 |
| Faults: the last one, after the last fixes | 1 of 1 | 1 | 0 |
| **Clean sweep: both suites in one eval, on the step 11 code** | **26 of 26** | **23** | **0** |

The clean sweep is the headline number: **23 of 26 runs passed (88%)**, 13 of
the 14 scenarios and 10 of the 12 fault runs, with **the money exactly right in
every run and no unsafe run**. Its own verifier agreed with the ground truth in
25 of 26. The three failures are in the findings above: one was fixed after the
sweep, and two are stated as limitations. Earlier rows show the history of
fixes, case by case. Model output varies from run to run, so these numbers are
evidence, not a guarantee.

## Develop

```bash
make check            # lint + typecheck + tests (what CI runs)
make format           # auto-format Python
make dashboard-build  # type-check and build the dashboard
```

## Models, APIs and frameworks

| What | Used for |
|---|---|
| **Gemini** (`gemini-3.5-flash-lite`, then `gemini-3.1-flash-lite`, then `gemini-flash-lite-latest` as fallbacks), through the Gemini REST API (`generateContent`) with function calling and image input | Every decision the operator makes, and the verifier's judgement. Any OpenAI-compatible API (Groq, OpenRouter, Ollama...) works by configuration. |
| **Playwright** (Chromium) | Operating the company's web apps; page snapshots, screenshots |
| **FastAPI**, Uvicorn, Server-Sent Events | The operator API, the live run stream, the sandbox apps |
| **Pydantic** v2, pydantic-settings | Typed models for the Company Pack, runs, tools and configuration |
| **SQLite** | Human requests, memory, the task queue (and the sandbox's own data) |
| httpx, PyYAML, Jinja2, Pillow | HTTP clients, the Company Pack's YAML, sandbox pages, generated photo attachments |
| **React** 19, TypeScript, Vite, Tailwind CSS, React Router, lucide-react, react-markdown | The dashboard |
| uv, ruff, mypy (strict), pytest, oxlint, GitHub Actions | Tooling and CI |

No agent framework: the runtime, prompts, tool layer and model clients are this
repository's own code.

## Assumptions

- **QuickBite is fictional.** Its people, data and money are seeded; no real
  company, brand, customer or credential is used.
- **The operator works like a new hire:** through the same web apps and logins
  a person would get, not through privileged database access. The sandbox's
  control API (reset, faults, ground truth) is for the eval harness and the
  dashboard's switchboard, never for the operator.
- **Amounts are in rupees**, stored in paise. Policy thresholds (₹500, coupon
  tiers) are QuickBite's, set in the Company Pack.
- **One supervisor role** approves and answers. Customers in the sandbox reply
  from a script.
- **One company per deployment**, running on one machine.

## Known limitations

- **A small model makes mistakes.** Flash-Lite repeats itself and misreads forms
  more than larger models do; the runtime's safeguards (hints, the loop check,
  verification, hand-over) catch these, but some correct work still ends in a
  hand-over. Results also vary from run to run, so eval results are evidence,
  not a guarantee.
- **Evals are small.** 14 scenarios and 7 fault profiles, sequential runs of
  about 25 model calls each; the free model quota limits how many fit in a day.
  Expected outcomes are written by hand.
- **Web apps only.** Desktop applications are out of scope (the tool layer is
  where an OS-level connector would go). Pages need reasonably semantic HTML for
  the snapshots to be useful.
- **Within its permissions, a model can be misled.** A customer's message is
  untrusted text in the prompt. Permissions, approvals and the request guard
  bound what a misled model can do, but they do not stop, say, a refund that
  policy allows being made for a claim that is false.
- **Verification is only as good as the contract.** The verifier checks the
  contract the operator wrote. If that leaves something out (the vague ticket
  never asked the customer), the verifier can pass incomplete work. The evals
  catch this because they judge against policy, not against the contract.
- **"Never pay twice" relies on the audit log** in normal use; only the evals
  compare against ground truth.
- **Before/after for a change** needs an earlier view of the same record.
- **The operator API and dashboard have no authentication**, and run on one
  machine with SQLite. Secrets are in `.env`; the sandbox's logins are in the
  Company Pack (sandbox only).
- **A few prompt examples use QuickBite's id formats**, so a new company's pack
  should come with its own examples.

## Next steps

- **Authentication and roles** for the API and dashboard; per-company secrets in
  a vault; scoped credentials per run.
- **Postgres and a workflow engine** (Temporal or similar) instead of SQLite and
  the in-house queue, for many workers and machines.
- **An isolated browser container per run.**
- **Larger evals, grown from real runs:** every hand-over and rejection becomes
  a new case; run them nightly against several models.
- **A stronger model for hard steps** (planning, verification), keeping a small
  one for routine clicks, chosen per call.
- **Real connectors** (Zendesk or Freshdesk, a payment gateway's API) next to the
  browser, with the same policy engine in front of both.
- **Desktop apps** through a vision-based computer-use connector.
- **A second Company Pack** (another company, another kind of work) to test
  generalization beyond support.
