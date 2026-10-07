# Autonomous Company Operator — Blueprint

> Built against the **CentrAlign AI Founding Engineer** problem statement:
> *"An AI operator that turns a company request into completed work."*
>
> Status: **DRAFT v2, for discussion.**

---

## 1. What the problem statement is actually asking for

Every design decision below traces back to one of these lines from the JD.

| Signal from the JD | What it means for this build |
|---|---|
| "A narrow system that genuinely demonstrates autonomy is considerably more valuable than a broad prototype that only simulates it" | **One business domain** (food-delivery customer support), made fully real: a real browser, real files, real (sandboxed) company systems, and no mocked tool results. |
| "AI-generated answers alone do not complete these tasks" | The AI is **not a chatbot replying to customers**. It is the support *employee* who resolves tickets by working inside the company's back-office systems. |
| "Working prototypes over presentations", "actual execution over simulated autonomy" | The agent changes state in systems it does not control. Those systems confirm the result on their own. |
| Core loop: **Goal → Understand → Plan → Execute → Observe → Adapt → Verify → Complete** | The runtime is an explicit state machine with exactly these phases, visible live in the dashboard and in the audit log. |
| "Use company context to identify the right sources, procedures, and permitted actions" | A **Company Pack** holds SOPs, the refund policy, the systems directory and permissions as *data*. The agent reads it, and nothing is hard-coded into prompts. |
| "Verify the outcome, return useful evidence" | A **separate verifier** re-reads the systems of record (refund ledger, order status, ticket state) and checks the success criteria. Evidence includes screenshots and read-back values. |
| "Ask for help when needed" | **Approval gates** (driven by policy, e.g. refunds above ₹500) and **clarification gates** (vague tickets). The run pauses durably and resumes after the human responds. |
| **Generalization**: "how much of the system can remain unchanged when given a different task?" | Eleven ticket types plus supervisor requests run on **one unchanged runtime**. Only the ticket and the Company Pack differ. |
| **Reliability**: "unexpected states, errors, retries, failures" | A **fault-injection switchboard** in the sandbox (timeouts, session expiry, moved buttons, ambiguous failures) shows recovery live. |
| Founding Engineer interview: "architecture, scalability, reliability, security, extensibility" | Typed, risk-tagged tools; a policy engine; a durable queue; an append-only audit log; an eval harness; and a written production path. |
| "Be prepared to explain, debug, or modify" | **No heavyweight agent framework.** We own the loop end to end. |

---

## 2. The scenario: QuickBite customer support operations

**Company:** *QuickBite*, a fictional food-delivery platform modelled on
apps like Swiggy and Zomato. It is fully sandboxed and uses no real brand,
data or credentials.

**The AI employee's role:** Customer Support Operations Associate.

**Why this domain fits:**
- Every ticket is a short request with most of the work left unstated.
- Resolving a ticket spans several internal systems.
- Money is involved, so permissions and approvals have real stakes.
- Ticket types vary a lot, which tests generalization for real.

### 2.1 Where the agent works: the sandbox systems

The agent works across **three separate QuickBite web apps**, as a real support
associate would. Each has its own login, UI and data, and the agent operates all
of them through a real browser.

| System | What it holds | What the agent does there |
|---|---|---|
| **Support Desk** | Tickets, customer messages, attachments (photos), ticket status | Read the ticket, reply to the customer, add internal notes, set the status, link duplicates |
| **Ops Admin** | Orders, items, the delivery timeline, restaurant and rider records, customer history (claims count) | Investigate what happened, cancel orders, raise incidents against riders or restaurants |
| **Payments Console** | Payments, the refund ledger, coupons | Check charges, issue refunds and coupons, confirm that refunds went through |

The **Company Pack** (for QuickBite) holds:
- the support SOPs (one per issue type);
- the refund and compensation policy, as machine-checkable rules;
- the approval thresholds;
- the reply tone guide;
- which systems the agent may use, and which actions it may take in each.

### 2.2 Ticket catalogue: everything the agent handles

Each ticket type exists to prove a different capability. All of them run on the
same runtime.

| # | Ticket type | Example customer message | What the agent does (none of it is spelled out in the ticket) | Human gate | What it proves |
|---|---|---|---|---|---|
| 1 | **Missing item** | "My Coke wasn't in the bag." | Finds the order in Ops Admin and confirms the item was ordered and charged. Checks the restaurant's packing log. Refunds the item's value in Payments. Replies and closes the ticket. | Only if the refund is above ₹500 | The basic loop working end to end across three systems |
| 2 | **Late delivery** | "Order took 1.5 hours!!" | Works out the actual delay from the delivery timeline (promised vs. delivered). Applies the tiered compensation policy (e.g. 30+ min late gets a ₹50 coupon, 60+ min gets ₹100). Issues the coupon. | No | Reasoning over data instead of trusting the claim (the customer may exaggerate) |
| 3 | **Wrong order delivered** | "I got someone else's food" + photo | Reads the photo attachment and compares it with the ordered items. Policy: wrong order means a full refund. Raises an incident against the restaurant. | **Yes**: full-order refund | Multimodal evidence, high-value approval |
| 4 | **Food quality** | "Biryani was cold and spilled." | Partial refund per policy. Flags the restaurant in Ops Admin. | Only above the threshold | Multiple actions from one ticket |
| 5 | **Charged for a cancelled order** | "Restaurant cancelled but money was debited." | Confirms the cancellation in Ops Admin and finds the charge in Payments. Checks whether an auto-refund already happened. Refunds only if it did not. | No | Cross-system reconciliation and **idempotency** (never refund twice) |
| 6 | **Double charge** | "I was charged twice." | Finds both transactions and confirms the duplicate. Refunds one. | **Yes**: payment reversal | Financial safety and evidence |
| 7 | **Refund status enquiry** | "Where is my refund?" | Looks up the refund in the ledger and replies with its status and expected date. Writes nothing to the money systems. | No | Read-only path: the agent knows when *not* to act |
| 8 | **Cancel or modify order** | "Please cancel my order." | Checks the order status. If the order has not been picked up, it cancels it and the refund follows. If already dispatched, policy forbids cancelling, so it explains and offers alternatives. | No | **State-dependent decisions** |
| 9 | **Rider behaviour complaint** | "The delivery guy was rude." | No money is involved. Raises an incident against the rider in Ops Admin, sends an empathetic reply, and escalates per the SOP. | No | Non-financial resolution path |
| 10 | **Suspicious repeat claimant** | (5th "missing item" claim this month) | Customer history triggers the fraud rule. The agent does **not** auto-refund. It routes the ticket for approval with a summary of the evidence. | **Yes**: fraud review | Policy and memory overriding a "nice" action |
| 11 | **Duplicate ticket** | Same issue re-filed after it was already resolved | Detects the earlier resolution, links the tickets and closes the new one without paying again. | No | Verification and idempotency |
| 12 | **Vague ticket** | "My order was bad." | Cannot determine the issue, so it **asks the customer a clarifying question**, pauses, and resumes when the reply arrives. | Clarification | Asking for help instead of guessing |

### 2.3 Supervisor requests: work beyond single tickets

The JD says *"company request"*, not only "customer ticket". A support
supervisor can also give the AI employee broader work orders:

| Supervisor request | What the agent does |
|---|---|
| "Clear all open late-delivery tickets from today." | Finds and filters the tickets in the queue, then works through them one by one in the background, with progress on the dashboard. |
| "How much did we refund today, by restaurant? Flag anything unusual." | Read-only investigation across Payments and Ops Admin. Returns a report with citations. |
| "Restaurant *Spice Hub* had a kitchen fire at 2 PM. Proactively compensate everyone whose order was cancelled." | Finds the affected orders and applies the policy to each. The total spend needs a **single batch approval**. |

**Generalization claim:** we will show that adding ticket type #13 means adding
an SOP to the Company Pack, with **zero runtime code changes**.

### 2.4 The approval rules (policy as data)

The approval rules live in the Company Pack as data, so they can be changed
without touching code.

- **Above ₹500:** any refund or coupon needs supervisor approval.
- **Full-order refunds:** always need approval.
- **Fraud flag:** customers with 3 or more claims in 30 days need approval.
- **Payment reversals** (such as a double charge) need approval.
- **Off-policy actions** need approval, and the agent must state why.
- **Batch actions:** a supervisor request that would affect more than 10 orders
  needs one batch approval.

If a human rejects an approval, the agent records the reason as a **company
fact**, so future runs learn from it.

---

## 3. Architecture

```
                         ┌─────────────────────────────────────┐
  Ticket / supervisor ─► │  Operator API  (FastAPI)            │ ◄── Dashboard (React): live loop,
  request                │  /runs /approvals /events (SSE)     │      queue, approvals, evidence
                         └──────────────┬──────────────────────┘
                                        │ enqueue
                                        ▼
                         ┌─────────────────────────────────────┐
                         │  Durable Task Queue + Workers       │  retries w/ backoff,
                         │  (resumable, checkpointed runs)     │  checkpoint after every step
                         └──────────────┬──────────────────────┘
                                        ▼
  ╔═════════════════════════════════════════════════════════════════════════╗
  ║                        OPERATOR RUNTIME (state machine)                 ║
  ║                                                                         ║
  ║  UNDERSTAND ─► PLAN ─► EXECUTE ─► OBSERVE ─► ADAPT ─┐                   ║
  ║      │           ▲                                  │                   ║
  ║      │           └────── replan on failure ─────────┘                   ║
  ║      ▼                                                                  ║
  ║  Task Contract:                         ┌──► VERIFY ─► COMPLETE         ║
  ║   • intended outcome                    │      │                        ║
  ║   • success criteria (checkable)  ──────┘      └─ fail ─► ADAPT         ║
  ║   • constraints / policies                                              ║
  ║                                                                         ║
  ║  Gates at any step:  ASK_HUMAN (clarify)  ·  AWAIT_APPROVAL (policy)    ║
  ╚═══════╤═══════════════╤═══════════════╤═══════════════╤═════════════════╝
          ▼               ▼               ▼               ▼
   ┌────────────┐  ┌────────────┐  ┌─────────────┐  ┌──────────────────┐
   │ Company    │  │ Tool       │  │ Memory      │  │ Policy & Perms   │
   │ Pack       │  │ Registry   │  │  • working  │  │ Engine           │
   │ SOPs,      │  │ typed,     │  │  • episodic │  │ risk tiers,      │
   │ policies,  │  │ risk-tagged│  │  • company  │  │ approval rules,  │
   │ systems    │  │ connectors │  │    facts    │  │ allow-lists      │
   └────────────┘  └─────┬──────┘  └─────────────┘  └──────────────────┘
                         │
         ┌───────────────┼──────────────────┬─────────────────┐
         ▼               ▼                  ▼                 ▼
   ┌───────────┐  ┌─────────────┐   ┌──────────────┐  ┌───────────────┐
   │ Browser   │  │ Files /     │   │ HTTP / API   │  │ Human channel │
   │ Playwright│  │ attachments │   │ connectors   │  │ (ask/approve) │
   │ a11y tree │  │ images, PDF │   │              │  │               │
   │ +screens. │  │ CSV         │   │              │  │               │
   └─────┬─────┘  └─────────────┘   └──────────────┘  └───────────────┘
         ▼
   ┌───────────────────────────────────────┐    ┌──────────────────────────┐
   │ SANDBOX: QuickBite                     │    │ Append-only Audit Log    │
   │  Support Desk · Ops Admin · Payments   │    │ every LLM decision +     │
   │  + FAULT INJECTION switchboard         │    │ rationale, every action, │
   └───────────────────────────────────────┘    │ every observation        │
                                                 └──────────────────────────┘
```

### 3.1 The runtime loop, applied to ticket #1 (missing item)

| Phase | What happens | Output |
|---|---|---|
| **Understand** | Classify the ticket as "missing item". Retrieve the missing-item SOP and the refund policy. Note the open questions: which item, and is it really missing? | **Task contract**. Success means: the refund equals the item price in the ledger, the customer has been replied to, and the ticket is resolved. |
| **Plan** | 1. Look up the order. 2. Confirm the item was charged. 3. Check the packing log. 4. Policy check. 5. Refund. 6. Reply. 7. Close the ticket. Each step has an expected postcondition. | A plan with postconditions. |
| **Execute** | Run the next step through the tool registry. Every call passes the policy engine first. | Tool result. |
| **Observe** | Did the postcondition hold? Capture an accessibility-tree snapshot and a screenshot. | An observation. |
| **Adapt** | Classify any failure as transient (retry), environmental (re-perceive the page), logical (replan) or ambiguous (check the state before retrying). Escalate to a human when the retry budget runs out. | A continue, retry, replan or escalate decision. |
| **Verify** | An independent verifier re-reads the ledger, the ticket and the order, and checks every success criterion. | Pass or fail per criterion, with evidence. |
| **Complete** | Summary, evidence bundle, audit trail, lessons saved to memory. | **Run report**. |

### 3.2 Key design decisions (the "why", for the interview)

1. **Contract-first execution.** Success criteria are written down *before*
   the agent acts. Verification is then a real check, not the LLM grading its
   own homework.
2. **The verifier is independent of the executor.** It has a different prompt
   and none of the executor's reasoning, and it reads ground truth directly.
   Deterministic checks come first, with an LLM judge only for fuzzy criteria
   such as "was the reply polite?".
3. **Check state before retrying a money action.** If a refund request times
   out, it might still have gone through. The agent re-reads the ledger before
   retrying, so it never double-refunds. This is the flagship reliability demo.
4. **Browser perception reads the accessibility tree first and pixels
   second.** This is deterministic, cheap and robust to cosmetic UI changes.
   Screenshots are kept for evidence and as a fallback.
5. **Tools are typed and risk-tagged.** Each tool declares a side-effect class
   (`read`, `write`, `money`, `irreversible`). The policy engine gates actions
   based on that data, not on prompt wording.
6. **The Company Pack is data, not prompts.** A new company or a new ticket
   type means a new SOP and new policy rules, not new code.
7. **Every step is checkpointed.** Runs survive an approval that takes hours
   and survive server restarts.
8. **The audit log is append-only and the UI is built from it.** Replay and
   debugging come for free, and so does compliance.
9. **Memory learns from feedback.** Approval rejections and human corrections
   become company facts that future runs retrieve.
10. **We own the agent loop** and use no framework, so every behaviour can be
    explained and changed live in the interview.
11. **The LLM provider can be swapped.** Changing provider means changing one
    config value. Every run is recorded and can be replayed in the dashboard,
    so auditors can inspect real runs without an API key.

---

## 4. Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| Agent runtime | **Python 3.12+** | Best ecosystem for Playwright, LLM SDKs and file handling. |
| LLM | Provider-agnostic interface. **The provider is decided at the "agent brain" step.** | Free-tier options are being considered. |
| Computer use | **Playwright** (Chromium): accessibility tree plus screenshots | A real browser, deterministic, headed mode for demos. |
| Backend API | **FastAPI** with Server-Sent Events | Async and typed, and the timeline updates live. |
| Persistence | **SQLite** | Zero-ops. Postgres is the documented production path. |
| Sandbox apps | FastAPI with server-rendered HTML (three apps) plus fault injection | Realistic "legacy internal tools" that the agent must operate through the UI. |
| **Dashboard** | **React + TypeScript + Vite + Tailwind + shadcn/ui**, with Recharts for analytics | Polished and feature-rich; dark and light themes. |
| Evals | pytest plus a scenario harness: ticket types × fault profiles → scorecard | Reliability becomes measurable. |

### 4.1 Dashboard features (polished, feature-rich)

- **Command centre:** submit a ticket or supervisor request. Includes KPIs:
  tickets resolved, auto-resolution rate, average time, refund spend, approval
  rate.
- **Live run view:** an animated phase tracker showing the current loop phase,
  the plan with each step's status, the agent's reasoning, tool calls, and a
  live browser screenshot stream.
- **Ticket queue:** filter by type and status, and launch runs in bulk.
- **Approval inbox:** shows what the agent wants to do, why, the policy rule
  that triggered the approval, and the evidence. Approve, reject with a reason,
  or edit.
- **Evidence viewer:** each verification criterion marked pass or fail, a
  screenshot gallery, before-and-after values, and the full audit timeline.
- **Run history and replay:** step through any past run decision by decision.
- **Company Pack browser:** view the SOPs, policies and learned company facts.
- **Reliability lab:** toggle fault profiles and run the eval suite, with a
  scorecard chart.

---

## 5. Repository layout

```
autonomous-company-operator/
├── README.md
├── docs/
│   ├── BLUEPRINT.md          # this file
│   ├── ARCHITECTURE.md       # deeper dive
│   └── DECISIONS.md          # ADR-style decision log
├── company_operator/         # the AI employee (Python)
│   ├── runtime/              # state machine, phases, task contract, checkpoints
│   ├── planning/             # planner, replanner, failure classifier
│   ├── verify/               # independent verifier + evidence
│   ├── tools/                # registry + browser / files / http / human tools
│   ├── memory/               # working, episodic, company facts
│   ├── policy/               # permission & approval engine
│   ├── llm/                  # provider-agnostic model client
│   ├── queue/                # durable task queue / workers
│   ├── audit/                # append-only event log
│   └── api/                  # FastAPI + SSE
├── dashboard/                # React + TS + Vite + Tailwind + shadcn/ui
├── company_packs/
│   └── quickbite/            # SOPs (md), policies (yaml), systems, permissions
├── sandbox/
│   └── quickbite/            # support_desk / ops_admin / payments + fault injection + seed data
├── evals/                    # scenarios × fault profiles → scorecard
└── tests/
```

---

## 6. Build plan

**Workflow:** after each step I show you what changed and **wait for your
approval**. Then I commit and push directly to `main`. Any step can be reverted.

| # | Step | Deliverable |
|---|---|---|
| 0 | **Blueprint** | This document, agreed |
| 1 | Project scaffold | Folder structure, Python and Node setup, config, lint and test tooling, README skeleton |
| 2 | Sandbox: QuickBite systems | Support Desk, Ops Admin and Payments apps, seed data for all 12 ticket types, fault-injection switchboard |
| 3 | Company Pack | SOPs, refund and compensation policy as rules, approval thresholds, systems and permissions |
| 4 | Tool layer and policy engine | Typed, risk-tagged tools (browser, files, HTTP, human), policy gate |
| 5 | Runtime core | Understand → Plan → Execute → Observe → Adapt, task contract, checkpoints, audit log |
| 6 | Agent brain (LLM) | Provider-agnostic client; **choose the provider here** |
| 7 | Verify and Complete | Independent verifier, evidence bundle, run report |
| 8 | Human-in-the-loop and memory | Approvals, clarifications, pause and resume, feedback-learned facts |
| 9 | Queue and background work | Durable queue, workers, supervisor batch requests |
| 10 | Dashboard | The full polished React app (section 4.1) |
| 11 | Reliability and evals | Fault profiles, eval harness, scorecard |
| 12 | Documentation and demo | README sections the JD requires, architecture, decisions, limitations, next steps, demo script |

---

## 7. Known limitations (stated honestly)

- **Native desktop apps are out of scope.** The agent operates browsers and
  files. The tool abstraction is ready for an OS-level computer-use connector.
- **Sandbox only.** QuickBite is fictional, and no real systems or credentials
  are used.
- **Single tenant.** Multi-tenancy, a secrets vault and RBAC are designed and
  documented but not built.

## 8. Production path (what's next)

- Postgres plus a durable workflow engine (Temporal or similar).
- Per-tenant secrets and scoped credentials.
- An isolated browser container for each run.
- Vision-based computer use for desktop apps.
- An eval dataset grown from real run traces.
- SOP synthesis from watching a human do a task once.
- Cost and latency budgets for each run.
- Real integrations: Zendesk/Freshdesk connectors and payment-gateway APIs.
