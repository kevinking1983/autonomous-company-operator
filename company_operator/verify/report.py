"""Run report: what was asked, what was done, what was proven, and what is still open.

Built only from the checkpoint (state.json) and the audit log (events.jsonl),
so any run, including an old or crashed one, can be reported on. Written as
report.json for the dashboard and report.md for people.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from company_operator.runtime.models import RunState

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
        }
        for o in state.observations
        if o.call.tool == "browser_click"
        and o.call.arguments.get("action")
        and "authorisation was withdrawn" not in o.output  # declared, but nothing was submitted
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
