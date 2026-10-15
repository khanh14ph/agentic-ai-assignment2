"""Assignment 2, Part D: the second pass before posting. This file is yours.

Two ways to read a drafted comment before it is posted (Lecture 2, slides 31 to 34).
The checker runs your planned way in each mode against scripted models, so keep
these exact signatures (checks/FORMATS.md, section 10.2). You may add keyword
arguments with defaults; the checker passes only these.

    verify(draft, ctx) -> {"passed": bool, "reasons": [str, ...]}

        The external mode: checks in code, against the repository and this run's
        own record. Four checks:
          1. the draft parses: a `label:` line with a known label, and a `file:`
             line with a path or none (section 1; harness.formats.parse_triage_comment);
          2. a path it names exists in the repository (ctx.root / "fixtures" / "repo");
          3. this run saw that path: a search_repo result or a read_file call in this
             run's trace returned it. Each tool_call line's `paths` field lists the
             files it returned; read the trace so far with
             harness.trace.read_trace(ctx.trace.path);
          4. the label and the file agree: a question names none, and every other
             label names a file.
        "reasons" says why each failed check failed; it is empty when all pass.
        verify reads only what the run itself can see: never the answer key, and
        never Assignment 1's grounded check in eval/. The checker flags a second
        pass that does.

    self_critique(client, draft, evidence, trace=None) -> {"decision": "accept" | "revise", "reason": str | None}

        The self mode: one model call that rereads the draft, given the issue and
        what the run's steps returned (`evidence`, a dict of your design), and
        accepts it or asks for a revision with a reason. Given a trace, trace the
        call: trace.model_call(n, response, purpose="critique").

    draft   the comment your executor drafted, the text post_comment would post
    ctx     the run's harness.runner.WayContext
    client  harness.client.ModelClient

The loop that uses them, its rewrite call and MAX_ROUNDS live in ways/planned.py.
Keep your prompts in prompts/.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.formats import LABELS, parse_triage_comment
from harness.trace import read_trace

ROOT = Path(__file__).resolve().parent.parent
CRITIQUE_PROMPT = "prompts/critique.md"
VERDICT_TOOL = "submit_verdict"
EVIDENCE_CHARS = 2000   # how much of each step result the critic gets to see


def verify(draft: str, ctx) -> dict:
    """The external checks. Only uses the repo and this run's own trace, never the answers."""
    label, file = parse_triage_comment(draft)
    # check 1: the draft parses (a known label, and a file line with a path or none)
    if label not in LABELS or file is None:
        return {"passed": False,
                "reasons": [f"the draft does not parse: it needs a 'label:' line with one of "
                            f"{', '.join(LABELS)} and a 'file:' line with a path or none"]}
    reasons = []
    if file != "none":
        # check 2: the file is really in the repo
        if not (ctx.root / "fixtures" / "repo" / file).is_file():
            reasons.append(f"the file {file} does not exist in the repository")
        # check 3: this run saw it (a search hit or a read_file in the trace so far)
        seen = {p for r in read_trace(ctx.trace.path) if r.get("type") == "tool_call"
                for p in r.get("paths") or []}
        if file not in seen:
            reasons.append(f"this run never saw {file}: no search result or read_file returned it")
    # check 4: label and file agree (question -> none, everything else -> a file)
    if label == "question" and file != "none":
        reasons.append("the label is question, so the file must be none")
    if label != "question" and file == "none":
        reasons.append(f"the label is {label}, so it must name a file, not none")
    return {"passed": not reasons, "reasons": reasons}


def verdict_tool() -> dict:
    """The critic answers by calling this, so the decision comes back as data."""
    return {
        "name": VERDICT_TOOL,
        "description": "Accept the draft comment, or ask for a revision and say why.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["decision", "reason"],
            "properties": {
                "decision": {"type": "string", "enum": ["accept", "revise"]},
                "reason": {"type": ["string", "null"],
                           "description": "What is wrong with the draft; null when you accept."},
            },
        },
    }


def describe_evidence(evidence: dict) -> str:
    """The issue and what each step returned, as text (shared with the rewrite in ways/planned.py)."""
    lines = [f"The issue:\n{evidence.get('issue') or '(not read)'}", "", "What the run's steps returned:"]
    for s in evidence.get("steps") or []:
        lines.append(f"--- step {s.get('id')}, {s.get('tool')} {json.dumps(s.get('args'))} ({s.get('status')})\n"
                     f"{str(s.get('result') or '')[:EVIDENCE_CHARS]}")
    return "\n".join(lines)


def self_critique(client, draft: str, evidence: dict, trace=None, step: int = 0) -> dict:
    """The self mode: the same model rereads its draft with the issue and the step results.
    It knows nothing new, so it can only catch what it missed the first time."""
    user = (describe_evidence(evidence) + "\n\nThe draft comment:\n" + draft
            + f"\n\nAccept the draft or ask for a revision, by calling {VERDICT_TOOL}.")
    response = client.create(system=(ROOT / CRITIQUE_PROMPT).read_text(),
                             messages=[{"role": "user", "content": user}], tools=[verdict_tool()])
    if trace is not None:
        trace.model_call(step, response, purpose="critique")
    call = next((c for c in response.tool_calls if c.name == VERDICT_TOOL), None)
    if call is None or call.input.get("decision") not in ("accept", "revise"):
        # no verdict -> nothing to act on, so the draft goes on as it is
        return {"decision": "accept", "reason": None}
    if call.input["decision"] == "accept":
        return {"decision": "accept", "reason": None}
    return {"decision": "revise", "reason": call.input.get("reason") or "the critic gave no reason"}

