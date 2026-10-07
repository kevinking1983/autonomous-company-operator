"""Run report: what was asked, what was done, what was proven, and what is still open.

Built only from the checkpoint (state.json) and the audit log (events.jsonl),
so any run, including an old or crashed one, can be reported on. Written as
report.json for the dashboard and report.md for people.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from company_operator.runtime.models import Observation, RunState

STATUS = {
    "completed": "✅ Completed and verified",
    "escalated": "⚠️ Escalated to a human",
    "failed": "❌ Failed",
    "awaiting_human": "⏸ Waiting for a human",
}


def load_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def outcome_text(output: str) -> str:
    """What the system said about a submission: the runtime's note, else the page's confirmation message."""
    lines = output.splitlines()
    note = next((ln[6:] for ln in lines if ln.startswith("Note: ")), "")
    if note:
        return note
    url = next((ln[5:] for ln in lines if ln.startswith("URL: ")), "")
    message = parse_qs(urlsplit(url).query).get("msg", [""])[0]
    return message or urlsplit(url).path


_REF = re.compile(r"\s*\[e\d+[^\]]*\]")
# Lines that describe the page rather than the record: navigation, banners and form controls whose
# values the operator typed (they show up "before" and are cleared "after", which is not a change).
_NOISE = re.compile(
    r"^\s*(URL: |Title: |HTTP status|Note: |\[status\]|(textbox|textarea|combobox|checkbox|button|link) )"
)
MAX_DIFF_LINES = 30


def _page_lines(output: str) -> list[str]:
    lines = output.splitlines()
    # The record starts at the page's heading; above it is the app's own header and navigation.
    start = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), 0)
    return [_REF.sub("", ln).rstrip() for ln in lines[start:] if ln.strip() and not _NOISE.match(ln)]


def _path(output: str) -> str | None:
    first = output.split("\n", 1)[0]
    return urlsplit(first[5:]).path if first.startswith("URL: ") else None


def record_diff(observations: list[Observation], index: int) -> list[dict[str, str]] | None:
    """How the record's page read before a change and after it: only the lines that differ.

    "After" is the page the submission landed on; "before" is the last time the operator saw that same
    page earlier in the run. When the submission landed on a record that did not exist before (a new
    refund, say), all of it is new. None when there is nothing sensible to compare.
    """
    after = observations[index]
    path = _path(after.output)
    if not path:
        return None
    before = next((o for o in reversed(observations[:index]) if _path(o.output) == path), None)
    if before is None:
        if not re.search(r"/[A-Z]+-\d+$", path):  # not a record page, e.g. a list
            return None
        return [{"op": "+", "text": line} for line in _page_lines(after.output)][:MAX_DIFF_LINES]
    diff = [
        {"op": line[0], "text": line[2:]}
        for line in difflib.ndiff(_page_lines(before.output), _page_lines(after.output))
        if line[:1] in "+-" and line[2:].strip()
    ]
    return diff[:MAX_DIFF_LINES]


def build_report(state: RunState, events: list[dict[str, Any]], run_dir: Path) -> dict[str, Any]:
    llm_calls = [e["data"] for e in events if e["type"] == "llm.call"]
    changes = [
        {
            "seq": o.seq,
            "step": o.step_id,
            "action": o.call.arguments.get("action"),
            "facts": o.call.arguments.get("facts", {}),
            "ok": o.ok,
            "error": o.error,
            "result": outcome_text(o.output),
            "diff": record_diff(state.observations, i) if o.ok else None,
        }
        for i, o in enumerate(state.observations)
        if o.call.tool == "browser_click"
        and o.call.arguments.get("action")
        and "request was sent by this click" not in o.output  # declared, but nothing was submitted
    ]
    human = [e["data"] for e in events if e["type"] in ("human.request", "human.decided", "human.answered")]
    evidence_dir = run_dir / "evidence"
    evidence = sorted(p.name for p in evidence_dir.iterdir()) if evidence_dir.exists() else []
    return {
        "run_id": state.run_id,
        "status": state.phase,
        "status_label": STATUS.get(state.phase, state.phase),
        "reason": state.outcome_reason,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "request": state.request.model_dump(),
        "contract": state.contract.model_dump() if state.contract else None,
        "plan": state.plan.model_dump() if state.plan else None,
        "changes": changes,
        "verification": state.verification.model_dump() if state.verification else None,
        "human": human,
        "pending_human": state.pending_human,
        "summary": state.summary,
        "evidence": evidence,
        "stats": {
            "observations": len(state.observations),
            "tool_calls": state.counters.tool_calls,
            "decisions": state.counters.decisions,
            "replans": state.counters.replans,
            "verify_rounds": state.counters.verify_rounds,
            "llm_calls": len(llm_calls),
            "input_tokens": sum(c.get("input_tokens", 0) for c in llm_calls),
            "output_tokens": sum(c.get("output_tokens", 0) for c in llm_calls),
            "models": sorted({c.get("model", "") for c in llm_calls}),
            "retries": sum(1 for e in events if e["type"] == "browser.retry"),
            "blocked": sum(1 for e in events if e["type"] == "guard.blocked"),
            "adaptations": [e["data"].get("rule") for e in events if e["type"] == "adapt.decision"],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    r = report
    lines = [
        f"# Run {r['run_id']}: {r['status_label']}",
        "",
        f"**Request** ({r['request']['source']}{', ticket ' + r['request']['ticket_id'] if r['request'].get('ticket_id') else ''}): "
        f"{r['request']['text']}",
    ]
    if r["reason"]:
        lines += ["", f"**Reason:** {r['reason']}"]
    if r["summary"]:
        lines += ["", "## Summary", "", r["summary"]]
    if r["contract"]:
        c = r["contract"]
        lines += ["", "## Task contract", "", f"**Outcome:** {c['outcome']}", ""]
        lines += [f"- `{cr['id']}` {cr['text']}" for cr in c["success_criteria"]]
        if c.get("assumptions"):
            lines += ["", "**Assumptions:** " + "; ".join(c["assumptions"])]
    if r["verification"]:
        v = r["verification"]
        lines += [
            "",
            f"## Verification: {'PASSED' if v['passed'] else 'FAILED'}",
            "",
            "| Criterion | Result | Evidence |",
            "|---|---|---|",
        ]
        lines += [
            f"| `{res['criterion_id']}` | {'✅' if res['passed'] else '❌'} | {res['evidence'].replace('|', '/').replace(chr(10), ' ')} |"
            for res in v["results"]
        ]
    if r["changes"]:
        lines += [
            "",
            "## Changes made",
            "",
            "| # | Step | Action | Facts | Result |",
            "|---|---|---|---|---|",
        ]
        for ch in r["changes"]:
            facts = ", ".join(f"{k}={v}" for k, v in ch["facts"].items())
            outcome = "ok" if ch["ok"] else f"**{ch['error']}**"
            lines.append(
                f"| {ch['seq']} | {ch['step'] or ''} | `{ch['action']}` | {facts} | {outcome} {ch['result']} |"
            )
    if r["plan"]:
        lines += ["", f"## Plan (version {r['plan']['version']})", ""]
        lines += [
            f"- [{s['status']}] **{s['id']}**: {s['goal']}" + (f" → {s['outcome']}" if s["outcome"] else "")
            for s in r["plan"]["steps"]
        ]
    if r["human"] or r["pending_human"]:
        lines += ["", "## People involved", ""]
        for h in r["human"]:
            lines.append("- " + ", ".join(f"{k}: {v}" for k, v in h.items() if v not in (None, "", [], {})))
        if r["pending_human"]:
            lines.append(f"- **Waiting on request {r['pending_human']}**")
    s = r["stats"]
    lines += [
        "",
        "## Run statistics",
        "",
        f"- Tool calls: {s['tool_calls']} · decisions: {s['decisions']} · replans: {s['replans']} · verification rounds: {s['verify_rounds']}",
        f"- Model calls: {s['llm_calls']} ({', '.join(s['models']) or 'none'}) · tokens in/out: {s['input_tokens']:,} / {s['output_tokens']:,}",
        f"- Automatic retries: {s['retries']} · requests blocked by policy: {s['blocked']} · adaptations: {', '.join(s['adaptations']) or 'none'}",
    ]
    if r["evidence"]:
        lines += ["", "## Evidence", ""] + [f"- [{name}](evidence/{name})" for name in r["evidence"]]
    return "\n".join(lines) + "\n"


def write_report(state: RunState, run_dir: Path) -> dict[str, Any]:
    report = build_report(state, load_events(run_dir), run_dir)
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return report
