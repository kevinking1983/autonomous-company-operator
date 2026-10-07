"""Independent verification: did the requested outcome actually happen?

The operator's own claims ("refund issued", "ticket resolved") are not
evidence. Before a run may complete, this verifier:

1. **Gathers evidence itself.** It finds every record mentioned in the task
   (ticket, order, refund, customer...) using the Company Pack's record types,
   then opens the pages that show those records' current state. It uses a
   fresh browser session with its own policy engine, which issues no grants,
   so it physically cannot change anything.
2. **Judges with a separate prompt.** The model sees only the success
   criteria and those pages, never the operator's plan, reasoning or claims,
   and must quote the evidence for each verdict. Anything not shown on the
   pages counts as not proven.
3. **Runs integrity checks in code.** Checks over the audit log that need no
   model, e.g. the same money movement never happened twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from company_operator.audit.log import EventLog
from company_operator.company_pack import CompanyPack
from company_operator.llm.base import LLMClient, LLMRequest, ToolSpec
from company_operator.policy import PolicyEngine
from company_operator.runtime.models import CriterionResult, RunState, Verification
from company_operator.tools import ToolContext
from company_operator.tools.browser import BrowserSession

MAX_PAGES = 12
MONEY_ACTIONS = frozenset({"payments.refund", "payments.issue_coupon"})
PAGE_CHARS = 9_000

SUBMIT_VERDICTS = ToolSpec(
    name="submit_verdicts",
    description="Submit one verdict per success criterion.",
    parameters={
        "type": "object",
        "required": ["results"],
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["criterion_id", "passed", "evidence"],
                    "properties": {
                        "criterion_id": {"type": "string"},
                        "passed": {"type": "boolean"},
                        "evidence": {
                            "type": "string",
                            "description": "Quote the exact values you relied on (ids, amounts, statuses, text), "
                            "or say what is missing.",
                        },
                    },
                },
            }
        },
    },
)

AUDITOR_SYSTEM = """You are {company}'s independent auditor. You did NOT do this work and you have not seen how it was done.
You are given a task's success criteria and pages read just now, read-only, from the company's systems of record.

For each criterion:
- PASSED only if the pages prove it. Quote the exact evidence: record ids, amounts, statuses, reasons, message text.
- FAILED if the pages contradict it, or do not show enough to prove it. Say exactly what is wrong or missing.
- Be strict about amounts (₹60.00 is not ₹600.00), reasons and statuses, about counts ("exactly one", "no other",
  "no new refund"), and about who did something: records created by "AI Operator" are this task's work; anything
  created by someone else or by "system:..." is not.
- Customer replies appear in the ticket conversation as agent messages; internal notes are marked "internal note".
Answer only by calling submit_verdicts with one result per criterion id."""


@dataclass(frozen=True)
class EvidencePage:
    record: str
    record_id: str
    url: str
    text: str
    screenshot: str


def find_records(pack: CompanyPack, state: RunState) -> list[tuple[str, str]]:
    """(record type, id) pairs mentioned in the task, criteria first so they win the page budget."""
    sources: list[str] = []
    if state.contract:
        sources += [c.text for c in state.contract.success_criteria] + [state.contract.outcome]
    sources += [state.request.text, state.request.ticket_id or "", *state.memory.values()]
    found: list[tuple[str, str]] = []
    for text in sources:
        for record in pack.records.values():
            for record_id in record.find(text):
                if (record.id, record_id) not in found:
                    found.append((record.id, record_id))
    return found


def integrity_checks(log: EventLog) -> list[CriterionResult]:
    """Checks over the audit log that need no model."""
    grants = {e.data["grant"]: e.data for e in log.of_type("policy.grant")}
    performed: list[tuple[str, str]] = []
    for event in log.of_type("guard.allowed"):
        grant = grants.get(event.data.get("grant") or "")
        if grant and grant["action"] in MONEY_ACTIONS:
            facts = {
                k: v
                for k, v in sorted(grant["facts"].items())
                if k not in ("customer_claims_30d", "full_order_refund")
            }
            performed.append((grant["action"], repr(facts)))
    duplicates = sorted({p for p in performed if performed.count(p) > 1})
    if duplicates:
        evidence = "; ".join(
            f"{action} {facts} performed {performed.count((action, facts))} times"
            for action, facts in duplicates
        )
        return [CriterionResult(criterion_id="integrity.money_once", passed=False, evidence=evidence)]
    detail = ", ".join(f"{a} {f}" for a, f in performed) or "no money was moved"
    return [
        CriterionResult(
            criterion_id="integrity.money_once",
            passed=True,
            evidence=f"Each money movement happened once: {detail}",
        )
    ]


class IndependentVerifier:
    def __init__(self, llm: LLMClient, max_pages: int = MAX_PAGES) -> None:
        self.llm = llm
        self.max_pages = max_pages

    async def verify(self, state: RunState, tools: ToolContext) -> Verification:
        assert state.contract is not None
        records = find_records(tools.pack, state)
        tools.log.emit("verify.started", records=[f"{kind}:{rid}" for kind, rid in records])
        pages = await self._gather(records, tools, round_no=state.counters.verify_rounds)
        verdicts = await self._judge(state, pages, tools)
        results = verdicts + integrity_checks(tools.log)
        return Verification(passed=all(r.passed for r in results), results=results)

    async def _gather(
        self, records: list[tuple[str, str]], tools: ToolContext, round_no: int = 1
    ) -> list[EvidencePage]:
        # A separate session with its own policy engine: no grants are ever issued, so every change is blocked.
        read_only = PolicyEngine(tools.pack, tools.log)
        source = tools.browser
        session = BrowserSession(
            tools.pack,
            read_only,
            tools.log,
            source.base_url,
            headless=source.headless,
            executable_path=source.executable_path,
            retry_delays=source.retry_delays,
        )
        pages: list[EvidencePage] = []
        seen_urls: set[str] = set()
        async with session:
            for kind, record_id in records:
                for view in tools.pack.records[kind].views:
                    if len(pages) >= self.max_pages:
                        break
                    url = session.url_for(view.system, view.path.format(id=record_id))
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    state = await session.open(url)
                    if state.error:
                        text = f"(could not read this page: {state.note or state.error})"
                    else:
                        text = state.snapshot
                    shot = (
                        tools.evidence_dir / f"verify{round_no}-{len(pages) + 1:02d}-{kind}-{record_id}.png"
                    )
                    await session.screenshot(shot)
                    pages.append(EvidencePage(kind, record_id, state.url, text, str(shot)))
                    tools.log.emit(
                        "verify.page", record=f"{kind}:{record_id}", url=state.url, error=state.error
                    )
        return pages

    async def _judge(
        self, state: RunState, pages: list[EvidencePage], tools: ToolContext
    ) -> list[CriterionResult]:
        assert state.contract is not None
        criteria = "\n".join(f"- {c.id}: {c.text}" for c in state.contract.success_criteria)
        evidence = "\n\n".join(
            f"### Page {i}: {p.record} {p.record_id} ({p.url})\n{p.text[:PAGE_CHARS]}"
            for i, p in enumerate(pages, 1)
        )
        prompt = f"## Success criteria\n{criteria}\n\n## Pages from the systems of record\n{evidence or '(none)'}\n"
        response = await self.llm.generate(
            LLMRequest(
                system=AUDITOR_SYSTEM.format(company=tools.pack.company.name),
                prompt=prompt,
                tools=[SUBMIT_VERDICTS],
                tool_mode="any",
                temperature=0.0,
                purpose="verify",
            )
        )
        tools.log.emit(
            "llm.call",
            purpose="verify",
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_s=round(response.latency_s, 2),
            images=0,
            calls=[{"name": c.name, "arguments": c.arguments} for c in response.calls],
            text=response.text[:500],
        )
        return parse_verdicts(state, response.calls[0].arguments if response.calls else {})


def parse_verdicts(state: RunState, arguments: dict[str, Any]) -> list[CriterionResult]:
    """One result per criterion; a criterion the auditor skipped is not proven, so it fails."""
    assert state.contract is not None
    given: dict[str, CriterionResult] = {}
    for item in arguments.get("results") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("criterion_id", "")).strip()
        match = re.match(r"^(c\d+)", cid)
        cid = match.group(1) if match else cid
        given[cid] = CriterionResult(
            criterion_id=cid,
            passed=item.get("passed") is True,
            evidence=str(item.get("evidence", "")).strip(),
        )
    return [
        given.get(c.id)
        or CriterionResult(criterion_id=c.id, passed=False, evidence="The auditor gave no verdict.")
        for c in state.contract.success_criteria
    ]
