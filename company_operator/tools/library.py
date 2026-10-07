"""The operator's tools. All generic: nothing here knows about refunds or tickets.

Company-specific behaviour comes from the Company Pack (which systems exist,
what each action means, what needs approval) and from the operator reading
the screens, never from bespoke functions like `refund_customer()`.
"""

from __future__ import annotations

import mimetypes
import re
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field

from company_operator.company_pack.models import Fact
from company_operator.tools.base import Tool, ToolContext, ToolInput, ToolRegistry, ToolResult
from company_operator.tools.browser import PageState

MAX_SUBTASKS = 25


def page_result(state: PageState, **data: Any) -> ToolResult:
    data = {"url": state.url, "status": state.status, "alerts": state.alerts, **data}
    if state.error:
        return ToolResult.failure(state.error, state.render(), data=data)
    return ToolResult.success(state.render(), data=data)


# ───────────────────────── browser ─────────────────────────


class OpenInput(ToolInput):
    system: str | None = Field(
        None, description="System id from the Company Pack, e.g. 'support', 'ops', 'payments'."
    )
    path: str = Field(
        "", description="Path within the system (e.g. 'tickets/TKT-1001' or 'orders?q=QB-1') or a full URL."
    )


class BrowserOpen(Tool[OpenInput]):
    name = "browser_open"
    description = (
        "Open a page in one of the company's systems and return a snapshot of it. Signs in automatically. "
        "Failed page loads are retried automatically."
    )
    input_model = OpenInput

    async def run(self, ctx: ToolContext, args: OpenInput) -> ToolResult:
        if args.system and args.system not in ctx.pack.systems:
            return ToolResult.failure(
                "invalid_input", f"Unknown system {args.system!r}. Known: {', '.join(ctx.pack.systems)}"
            )
        return page_result(await ctx.browser.open(ctx.browser.url_for(args.system, args.path)))


class SnapshotInput(ToolInput):
    pass


class BrowserSnapshot(Tool[SnapshotInput]):
    name = "browser_snapshot"
    description = "Return a fresh snapshot of the current page, with new element refs."
    input_model = SnapshotInput

    async def run(self, ctx: ToolContext, args: SnapshotInput) -> ToolResult:
        return page_result(await ctx.browser.state())


NOT_SENT = "no {action} request was sent by this click"


def element_ref(value: str) -> str:
    """Accept a ref the way models tend to write it: "e12", "[e12]", "[e12 -> /path]" or "ref e12"."""
    found = re.search(r"\be\d+\b", value)
    return found.group(0) if found else value.strip()


# An element ref from the latest snapshot, normalised before any tool sees it.
Ref = Annotated[str, AfterValidator(element_ref)]


class ClickInput(ToolInput):
    ref: Ref = Field(description="Element ref from the latest snapshot, e.g. 'e12'.")
    action: str | None = Field(
        None,
        description="If this click submits a change, the Company Pack action it performs (e.g. 'payments.refund'). "
        "Required for anything that is not a read; undeclared changes are blocked.",
    )
    facts: dict[str, Fact] = Field(
        default_factory=dict,
        description="Facts the action is authorised against, e.g. {payment_id, amount, reason, customer_claims_30d}.",
    )


class BrowserClick(Tool[ClickInput]):
    name = "browser_click"
    description = (
        "Click a link, button or menu. To submit a change, declare the action and its facts: policy is checked "
        "first and the submitted form must match what was declared."
    )
    input_model = ClickInput

    async def run(self, ctx: ToolContext, args: ClickInput) -> ToolResult:
        grant = None
        if args.action:
            grant = ctx.policy.find_grant(args.action, args.facts)
            if grant is None:
                decision, grant = ctx.policy.authorize(args.action, args.facts)
                if decision.outcome == "deny":
                    return ToolResult.failure(
                        "policy", "Denied by company policy: " + "; ".join(decision.reasons)
                    )
                if decision.outcome == "needs_approval":
                    return ToolResult.failure(
                        "needs_approval",
                        f"{args.action} needs supervisor approval ({', '.join(decision.rules)}): "
                        + "; ".join(decision.reasons)
                        + ". Use request_approval with the same action and facts, then click again once approved.",
                        data={"rules": decision.rules},
                    )
        sent_before = ctx.policy.sent.get(args.action, 0) if args.action else 0
        state = await ctx.browser.click(args.ref)
        wrong = [a for a in state.blocked_actions if a != args.action]
        if args.action and state.error == "policy" and wrong and len(wrong) == len(state.blocked_actions):
            # Not a policy question: the control does something other than what was declared.
            declared = ctx.pack.action(args.action)
            where = ", ".join(f"{r.method} {r.path}" for r in declared.requests)
            state.error = "invalid_input"
            state.note = (
                f"Nothing was sent: this control submits {', '.join(sorted(set(wrong)))}, not the {args.action} "
                f"you declared. {args.action} is: {declared.description} (it is submitted as {where}). "
                "Find the control that does that, which may be on another page."
            )
        if grant and args.action and ctx.policy.sent.get(args.action, 0) == sent_before and not state.error:
            # Nothing matching the declared action was submitted, e.g. the click opened a confirmation page,
            # or the browser refused to submit a form with a required field left empty.
            invalid = await ctx.browser.invalid_fields()
            if grant.approved:
                # A person's approval is kept: it still allows only this exact request, once.
                outcome = "the approval still stands; declare it again on the click that submits it."
            else:
                ctx.policy.revoke(grant.id)  # don't leave a live grant lying around
                outcome = "the authorisation was withdrawn."
            if invalid:
                state.error = "invalid_input"
                outcome = (
                    "the form was not submitted because these fields are not valid: "
                    + "; ".join(invalid)
                    + f". Fix them, then click again. Also: {outcome}"
                )
            state.note = (state.note + " " if state.note else "") + (
                f"Note: {NOT_SENT.format(action=args.action)}; {outcome}"
            )
        return page_result(state, action=args.action, grant=grant.id if grant else None)


class FillInput(ToolInput):
    ref: Ref = Field(description="Ref of a textbox or textarea, e.g. 'e12'.")
    value: str


class BrowserFill(Tool[FillInput]):
    name = "browser_fill"
    description = "Type a value into a text field (replacing what is there). Does not submit anything."
    input_model = FillInput

    async def run(self, ctx: ToolContext, args: FillInput) -> ToolResult:
        return page_result(await ctx.browser.fill(args.ref, args.value))


class SelectInput(ToolInput):
    ref: Ref = Field(description="Ref of a combobox (select), e.g. 'e12'.")
    option: str = Field(description="Visible option text or option value.")


class BrowserSelect(Tool[SelectInput]):
    name = "browser_select"
    description = "Choose an option in a dropdown. Does not submit anything."
    input_model = SelectInput

    async def run(self, ctx: ToolContext, args: SelectInput) -> ToolResult:
        return page_result(await ctx.browser.select(args.ref, args.option))


class ScreenshotInput(ToolInput):
    label: str = Field(description="Short name for what this screenshot proves, e.g. 'refund-RF-70012'.")


class BrowserScreenshot(Tool[ScreenshotInput]):
    name = "browser_screenshot"
    description = "Save a full-page screenshot of the current page as evidence for the run report."
    input_model = ScreenshotInput

    async def run(self, ctx: ToolContext, args: ScreenshotInput) -> ToolResult:
        index = len(list(ctx.evidence_dir.glob("[0-9][0-9]-*.png"))) + 1
        slug = re.sub(r"[^a-z0-9]+", "-", args.label.lower()).strip("-")[:60] or "page"
        path = await ctx.browser.screenshot(ctx.evidence_dir / f"{index:02d}-{slug}.png")
        return ToolResult.success(f"Saved screenshot {path.name} of {ctx.browser.page.url}", evidence=[path])


# ───────────────────────── files ─────────────────────────


class AttachmentInput(ToolInput):
    url: str = Field(description="Link to the attachment, as shown in the page snapshot.")


class ViewAttachment(Tool[AttachmentInput]):
    name = "view_attachment"
    description = "Download an attachment (e.g. a customer's photo), keep it as evidence and show it to you."
    input_model = AttachmentInput

    async def run(self, ctx: ToolContext, args: AttachmentInput) -> ToolResult:
        url = ctx.browser.url_for(None, args.url) if args.url.startswith("/") else args.url
        if urlsplit(url).netloc != ctx.browser.origin:
            return ToolResult.failure(
                "policy", "Attachments can only be fetched from the company's own systems."
            )
        status, content_type, body = await ctx.browser.fetch_bytes(url)
        if status == 404:
            return ToolResult.failure("not_found", f"No attachment at {args.url}")
        if status >= 400:
            return ToolResult.failure("transient", f"Could not download attachment ({status})")
        mime = content_type.split(";")[0].strip()
        extension = mimetypes.guess_extension(mime) or ".bin"
        path = ctx.evidence_dir / f"attachment-{urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]}{extension}"
        path.write_bytes(body)
        ctx.log.emit("evidence.saved", kind="attachment", path=str(path), url=url)
        is_image = mime.startswith("image/")
        return ToolResult.success(
            f"Downloaded {mime} attachment ({len(body):,} bytes) to {path.name}."
            + (" The image is attached for you to look at." if is_image else ""),
            evidence=[path],
            images=[path] if is_image else [],
            data={"content_type": mime, "bytes": len(body)},
        )


# ───────────────────────── policy and people ─────────────────────────


class PolicyInput(ToolInput):
    action: str = Field(description="Company Pack action id, e.g. 'payments.refund'.")
    facts: dict[str, Fact] = Field(default_factory=dict)


class CheckPolicy(Tool[PolicyInput]):
    name = "check_policy"
    description = (
        "Ask whether an action would be allowed, need approval, or be denied, without doing anything. "
        "Use while planning."
    )
    input_model = PolicyInput

    async def run(self, ctx: ToolContext, args: PolicyInput) -> ToolResult:
        decision = ctx.policy.evaluate(args.action, args.facts)
        text = f"{args.action}: {decision.outcome}. " + "; ".join(decision.reasons)
        return ToolResult.success(text, data={"outcome": decision.outcome, "rules": decision.rules})


class ApprovalInput(PolicyInput):
    justification: str = Field(
        description="What you want to do and why, with the evidence a supervisor needs."
    )


class RequestApproval(Tool[ApprovalInput]):
    name = "request_approval"
    description = "Ask a supervisor to approve an action that policy says needs approval."
    input_model = ApprovalInput

    async def run(self, ctx: ToolContext, args: ApprovalInput) -> ToolResult:
        decision = ctx.policy.evaluate(args.action, args.facts)
        if decision.outcome == "allow":
            return ToolResult.success(f"{args.action} does not need approval; go ahead.")
        if decision.outcome == "deny":
            return ToolResult.failure(
                "policy", "Cannot be approved, it is denied: " + "; ".join(decision.reasons)
            )
        request = ctx.human.request_approval(
            decision, args.justification, context={"page": ctx.browser.current_url}
        )
        evidence = []
        if ctx.browser.current_url:
            evidence.append(await ctx.browser.screenshot(ctx.evidence_dir / f"approval-{request.id}.png"))
        return ToolResult.success(
            f"Approval requested ({request.id}) for {args.action}: {', '.join(decision.rules)}. "
            "Wait for the decision before acting.",
            data={"request_id": request.id, "pending": True},
            evidence=evidence,
        )


class AskInput(ToolInput):
    question: str
    audience: str = Field("supervisor", description="'supervisor' for internal questions.")


class AskHuman(Tool[AskInput]):
    name = "ask_human"
    description = (
        "Ask your supervisor a question when you are blocked or something is ambiguous. "
        "To ask a customer, reply on their ticket instead."
    )
    input_model = AskInput

    async def run(self, ctx: ToolContext, args: AskInput) -> ToolResult:
        request = ctx.human.ask(args.question, args.audience)
        return ToolResult.success(
            f"Question sent ({request.id}). Wait for the answer.",
            data={"request_id": request.id, "pending": True},
        )


class WaitInput(ToolInput):
    record_id: str = Field(
        description="Id of the record where the reply will appear, e.g. the ticket TKT-1012."
    )
    about: str = Field(description="What you asked, and what answer you are waiting for.")


class WaitForReply(Tool[WaitInput]):
    name = "wait_for_reply"
    description = (
        "After asking someone outside the company (e.g. the customer, by replying on their ticket) a question, "
        "pause until they answer on that record. You resume with their answer."
    )
    input_model = WaitInput

    async def run(self, ctx: ToolContext, args: WaitInput) -> ToolResult:
        url = record_url(ctx, args.record_id)
        if url is None:
            return ToolResult.failure(
                "invalid_input", f"{args.record_id!r} is not a record id this company uses."
            )
        state = await ctx.browser.open(url)
        if state.error:
            return ToolResult.failure(state.error, f"Could not read {args.record_id}: {state.note}")
        request = ctx.human.wait_for_reply(
            args.about, context={"record": args.record_id, "url": url, "baseline": state.snapshot}
        )
        return ToolResult.success(
            f"Waiting for a reply on {args.record_id} ({request.id}). The run pauses until it arrives.",
            data={"request_id": request.id, "pending": True},
        )


class SubTask(ToolInput):
    text: str = Field(
        description="The request for this piece of work, e.g. 'Resolve support ticket TKT-1015.'"
    )
    ticket_id: str | None = Field(None, description="The ticket it concerns, if any.")


class DelegateInput(ToolInput):
    tasks: list[SubTask] = Field(min_length=1, max_length=MAX_SUBTASKS)
    about: str = Field(description="What these sub-tasks achieve together.")


class DelegateTasks(Tool[DelegateInput]):
    name = "delegate_tasks"
    description = (
        "Split a large request into independent sub-tasks (e.g. one per ticket) that are worked on separately, "
        "each with its own verification. Your run pauses until they are all finished, then you see their outcomes."
    )
    input_model = DelegateInput

    async def run(self, ctx: ToolContext, args: DelegateInput) -> ToolResult:
        if ctx.queue is None:
            return ToolResult.failure(
                "invalid_input", "Sub-tasks need the task queue: run this request through a worker."
            )
        children = [
            ctx.queue.enqueue(
                t.text,
                ticket_id=t.ticket_id,
                source="supervisor",
                requested_by=f"task {ctx.task_id}",
                parent_id=ctx.task_id,
            )
            for t in args.tasks
        ]
        request = ctx.human.wait_for_tasks(args.about, [c.id for c in children])
        listing = ", ".join(f"{c.id} ({c.ticket_id or c.text[:40]})" for c in children)
        return ToolResult.success(
            f"Delegated {len(children)} sub-tasks: {listing}. Waiting for them to finish ({request.id}).",
            data={"request_id": request.id, "pending": True, "tasks": [c.id for c in children]},
        )


def record_url(ctx: ToolContext, record_id: str) -> str | None:
    for record in ctx.pack.records.values():
        if record.find(record_id) == [record_id.strip().upper()]:
            view = record.views[0]
            return ctx.browser.url_for(view.system, view.path.format(id=record_id.strip().upper()))
    return None


def default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            BrowserOpen(),
            BrowserSnapshot(),
            BrowserClick(),
            BrowserFill(),
            BrowserSelect(),
            BrowserScreenshot(),
            ViewAttachment(),
            CheckPolicy(),
            RequestApproval(),
            AskHuman(),
            WaitForReply(),
            DelegateTasks(),
        ]
    )
