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

import itertools
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
    """Checks over the audit log that need no model: no money movement happened twice.

    A submission is "confirmed" when the system answered it, and "uncertain" when it answered 5xx (the
    money may or may not have moved). Two confirmed submissions of the same movement fail. An uncertain one
    followed by another is acceptable only if the operator re-read the systems in between (the runtime
    requires it), so the retry was a decision made on fresh evidence, not a blind repeat.
    """
    # Grant ids restart when a paused run resumes in a new process, so match each submission to the grant
    # most recently issued under that id, in log order.
    grants: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []  # in order: key, status, and whether a read came after it
    for event in log.events:
        d = event.data
        if event.type == "policy.grant":
            grants[d["grant"]] = d
        elif event.type == "guard.allowed":
            grant = grants.get(d.get("grant") or "")
            if grant and grant["action"] in MONEY_ACTIONS:
                facts = {
                    k: v
                    for k, v in sorted(grant["facts"].items())
                    if k not in ("customer_claims_30d", "full_order_refund")
                }
                attempts.append(
                    {
                        "key": (grant["action"], repr(facts)),
                        "path": d["path"],
                        "status": "confirmed",
                        "reread": False,
                    }
                )
        elif (
            event.type == "browser.uncertain"
            and attempts
            and str(d.get("url", "")).endswith(attempts[-1]["path"])
        ):
            attempts[-1]["status"] = "uncertain"
        elif event.type == "policy.grant_released" and attempts:
            attempts[-1]["status"] = "rejected"  # the system refused it: nothing moved
        elif event.type == "tool.call" and d.get("tool") in ("browser_open", "browser_snapshot") and attempts:
            attempts[-1]["reread"] = True

    problems: list[str] = []
    notes: list[str] = []
    for key in dict.fromkeys(a["key"] for a in attempts):
        mine = [a for a in attempts if a["key"] == key and a["status"] != "rejected"]
        confirmed = [a for a in mine if a["status"] == "confirmed"]
        action, facts = key
        if len(confirmed) > 1:
            problems.append(f"{action} {facts} performed {len(confirmed)} times")
        blind = [a for a, _ in itertools.pairwise(mine) if a["status"] == "uncertain" and not a["reread"]]
        if blind:
            problems.append(f"{action} {facts} was retried after an uncertain attempt without checking first")
        elif any(a["status"] == "uncertain" for a in mine):
            notes.append(
                f"{action} {facts}: an attempt's outcome was unknown (the system answered 5xx); "
                "the operator re-read the records before acting again"
            )
    if problems:
        return [
            CriterionResult(criterion_id="integrity.money_once", passed=False, evidence="; ".join(problems))
        ]
    done = [f"{a} {f}" for a, f in dict.fromkeys(x["key"] for x in attempts if x["status"] == "confirmed")]
    detail = ", ".join(done) or "no money was moved"
    return [
        CriterionResult(
            criterion_id="integrity.money_once",
            passed=True,
            evidence=f"Each money movement happened once: {detail}"
            + (f". {'; '.join(notes)}" if notes else ""),
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
                    shot = (
                        tools.evidence_dir / f"verify{round_no}-{len(pages) + 1:02d}-{kind}-{record_id}.png"
                    )
                    try:
                        state = await session.open(url)
                        text = (
                            state.snapshot
                            if not state.error
                            else f"(could not read this page: {state.note or state.error})"
                        )
                        page_url, error = state.url, state.error
                        await session.screenshot(shot)
                    except Exception as exc:  # an unreadable page is "not verified", never a crashed run
                        page_url, error = url, "transient"
                        text = f"(could not read this page: {type(exc).__name__})"
                    pages.append(EvidencePage(kind, record_id, page_url, text, str(shot)))
                    tools.log.emit("verify.page", record=f"{kind}:{record_id}", url=page_url, error=error)
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
