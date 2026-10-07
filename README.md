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

**Status:** step 2 of 13 (QuickBite sandbox). See the
[build plan](docs/BLUEPRINT.md#6-build-plan).

---

## The core loop

```
Goal → Understand → Plan → Execute → Observe → Adapt → Verify → Complete
```

## Repository layout

| Path | What lives there |
|---|---|
| `company_operator/` | The AI employee: runtime, planning, verification, tools, memory, policy, LLM client, queue, audit log, API |
| `sandbox/quickbite/` | QuickBite's sandboxed systems: Support Desk, Ops Admin, Payments Console |
| `company_packs/quickbite/` | QuickBite's SOPs, policies, systems and permissions, stored as data |
| `dashboard/` | React + TypeScript dashboard |
| `evals/` | Scenario harness and reliability scorecard |
| `tests/` | Test suite |
| `docs/` | Blueprint, architecture and design decisions |

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 22+.

```bash
cp .env.example .env
make install      # Python deps, Chromium for Playwright, dashboard deps
```

## Run

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
