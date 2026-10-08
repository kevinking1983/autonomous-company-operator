# Decisions

The choices that shaped the operator, in the order they were made: what was
decided, why, and what it costs. Several were revised once the operator met a
real model and real failures. Those revisions are recorded here as well.

### 1. One domain, made real: QuickBite customer support

**Decision.** Build one company's support operations fully: three separate
back-office apps with their own logins, seeded data and faults. The operator
works them through a real browser, the way a new hire would.

**Why.** The problem statement values "a narrow system that genuinely
demonstrates autonomy" over a broad simulation. Support work leaves most of the
job unstated, spans several systems, and involves money, so permissions and
approvals have real stakes.

**Cost.** Desktop apps and other kinds of company work are not covered (see the
README's limitations).

### 2. The company is data: the Company Pack

**Decision.** SOPs, compensation and approval policy, permissions, systems and
record types live in files under `company_packs/quickbite/`. The runtime has no
code for any ticket type.

**Why.** Generalization: a new kind of ticket is a new SOP and maybe a policy
rule, not new code. People can read and change the policy without reading
Python.

**Cost.** The model has to read and apply prose SOPs, which a weaker model does
less reliably than hard-coded logic would. A few prompt examples still use
QuickBite's id formats.

### 3. The contract is written before acting

**Decision.** Understand ends with a task contract: the intended outcome and
checkable success criteria, written before anything changes. Verification
checks the contract. A person's answer or rejection revises it.

**Why.** Otherwise "done" is whatever the model says at the end. Writing it down
first makes verification a real check.

### 4. Declare, then act: grants and a request guard

**Decision.** To change anything, the operator declares the action and its facts
(`payments.refund {payment_id, amount, reason}`). The policy engine decides, and
on "allow" issues a grant. Every non-GET request the browser makes is checked
against live grants, including the path and the submitted form values.

**Why.** Safety that does not depend on the model behaving. The operator cannot
take an undeclared action, exceed its permissions, or declare ₹60 and submit
₹600, whatever its prompt says.

**Revised in step 10.** A person's approval stays valid through clicks that send
nothing (a review page), but grants issued by policy are withdrawn after such
clicks. Submissions are counted per action, because the guard may use a
different grant than the click's own.

### 5. Writes are never retried blindly

**Decision.** Reads retry automatically. A write that ends in a 5xx is
*uncertain*. The runtime then requires a fresh read of the record before any
other step, and the "no money moved twice" check fails any retry made without
one.

**Why.** A refund that times out may well have gone through. Paying twice is the
worst failure a support operator can make.

### 6. An independent verifier that cannot change anything

**Decision.** Verification uses a fresh browser session with its own policy
engine and no grants, a different prompt, and none of the executor's
reasoning. It re-reads the records the run touched and passes a criterion only
with evidence. Checks that need no model (money moved once) run in code.

**Why.** An executor grading its own work is not verification.

**Revised in step 11.** The verifier never crashes a run: an unreadable page
counts as "not verified".

### 7. Ask a person, durably

**Decision.** Approvals, questions and waits for customers are stored requests.
The run checkpoints and stops, and any process resumes it once answered.
Before stopping for good, the operator hands the ticket over with a note.

**Why.** People answer in hours, not seconds. A hand-over means nobody has to
reconstruct what happened.

**Added in step 10.** A supervisor can approve a *lower* amount, never a higher
one. The operator is told and revises its contract and reply.

### 8. Learn from people

**Decision.** A rejection's reason can be kept as a company fact, and every
finished run is kept as an episode. Both are given to later runs.

**Why.** "Don't refund repeat claimants when the packing log shows it was
packed" should only have to be said once.

### 9. A sub-task's escalation belongs to a person

**Decision.** When a supervisor request is split into sub-tasks and one of them
escalates, the parent reports it and keeps its hands off that ticket.

**Why.** The parent overriding a sub-task that stopped for a reason undoes the
safety the sub-task just exercised. (Option B in the step 9 discussion.)

### 10. We own the loop

**Decision.** No agent framework. The state machine, prompts, tool layer and
model clients are this repository's code.

**Why.** Every behaviour can be explained, debugged and changed, which the role
asks for. Most fixes in step 11 were a few lines in the runtime, possible
because nothing was hidden behind a framework.

### 11. Gemini Flash-Lite, behind a provider-neutral interface

**Decision.** Gemini through its REST API with function calling, on the free
tier, with a chain of models (Flash-Lite 3.5, then 3.1, then the latest) and
cooldowns on rate limits. Any OpenAI-compatible provider works by
configuration.

**Why.** Free, with function calling and images (customers attach photos).
Small models make the runtime's job harder, so its safeguards are tested against
a model that makes mistakes.

**Cost.** The free daily quota limits how many runs and evals fit in a day.
Flash-Lite repeats itself and misreads forms more than larger models do.

### 12. Polished dashboard, few dependencies

**Decision.** React, TypeScript, Vite and Tailwind, with our own small
components and one hand-built chart. shadcn/ui and Recharts, in the original
blueprint, were dropped.

**Why.** The dashboard needs about a dozen components and one chart. Owning them
keeps the bundle and the dependency list small.

### 13. Evals scored on ground truth

**Decision.** Each eval run resets the sandbox, injects a fault profile, runs one
ticket with a fresh memory and a scripted supervisor, then compares the
sandbox's records before and after against what policy says should happen.
The operator's own account never counts.

**Why.** "It said it refunded ₹60" and "one ₹60 refund exists, on the right
payment, once" are different claims. Only the second is evidence.

**Cost.** Each case's expectations are worked out by hand from the policy and the
seed data. The runs are sequential (they share one sandbox) and cost about 25
model calls each.

### 14. Fix the runtime, never the case

**Decision.** When an eval failed, the fix went into the runtime or the tools,
generically, with a regression test. Cases and expectations were not loosened
to make a run pass.

**Why.** A case adjusted to pass a run is no longer measuring anything.
