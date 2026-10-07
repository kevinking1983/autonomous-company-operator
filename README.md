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

**Status:** step 7 of 13 (independent verification and run reports). See the
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

Other commands:

```bash
make sandbox      # QuickBite's back-office apps on http://127.0.0.1:8100
make api          # operator API on http://127.0.0.1:8000
make dashboard    # dashboard on http://localhost:5173
```

The sandbox's systems, logins, scenario catalogue and fault injection are
documented in [`sandbox/quickbite/README.md`](sandbox/quickbite/README.md).

| Support Desk | Ops Admin |
|---|---|
| ![Support Desk ticket](docs/screenshots/support-ticket-wrong-order.png) | ![Ops Admin order](docs/screenshots/ops-order-missing-item.png) |

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
