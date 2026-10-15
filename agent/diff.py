"""Assignment 2, Part E: the diff of the plan against what ran. This file is yours.

"The one people skip" (Lecture 2, slide 54). The checker calls it on slide 51's run
in fixtures/traces/s49/, so keep this exact signature (checks/FORMATS.md, section
10.2):

    diff(plan, trace) -> {"steps": {step_id: label}, "extra": [step numbers]}

    steps   every plan step, labelled
              executed   it ran, in plan order
              skipped    it never ran
              reordered  it ran, out of plan order
    extra   the step numbers of tool_call lines that match no plan step

How a call matches a step is yours to decide: by its tool, and by any arguments
the plan fixes (an argument written "$fill" is fixed by nothing). Say how in your
report. Mind slide 51's run: it calls run_tests twice, and only one of those calls
can belong to each run_tests step.
"""

from __future__ import annotations

import json

FILL = "$fill"


def tool_calls(trace: list[dict]) -> list[dict]:
    return [r for r in trace if r.get("type") == "tool_call"]


def step_args(step: dict) -> dict:
    """The step's arguments, whether the plan wrote them as args or as args_json."""
    if isinstance(step.get("args"), dict):
        return step["args"]
    try:
        args = json.loads(step.get("args_json") or "{}")
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def fits(step: dict, call: dict) -> bool:
    """A call fits a step if it is the same tool and every argument the plan fixed
    (anything not "$fill") has the same value in the call."""
    if not step.get("tool") or step["tool"] != call.get("name"):
        return False
    given = call.get("input") or {}
    return all(given.get(k) == v for k, v in step_args(step).items() if v != FILL)


def assign(plan: dict, trace: list[dict]) -> list[tuple[dict, object]]:
    """For each tool call, the id of the plan step it belongs to, or None (extra).
    If the call has plan_step (my planned way writes it), that decides, and a retry of
    the same step still belongs to it. If not, the call goes to the first step, in plan
    order, that it fits and that has no call yet. So slide 51's second run_tests (with
    test=test_export_csv) can only be step 5, and the second "export" search is extra."""
    steps = plan["steps"]
    ids = {str(s["id"]): s["id"] for s in steps}
    taken, out = set(), []
    for call in tool_calls(trace):
        if call.get("plan_step") is not None:
            sid = ids.get(str(call["plan_step"]))
        else:
            step = next((s for s in steps if s["id"] not in taken and fits(s, call)), None)
            sid = step["id"] if step else None
        if sid is not None:
            taken.add(sid)
        out.append((call, sid))
    return out


def match_calls(plan: dict, trace: list[dict]) -> dict:
    """{step id: its call}. With retries it is the last try, since that is the step's result."""
    return {sid: call for call, sid in assign(plan, trace) if sid is not None}


def diff(plan: dict, trace: list[dict]) -> dict:
    """Label every plan step executed / skipped / reordered, and list the extra calls."""
    first = {}   # step id -> position of its first call in the trace
    extra = []
    for pos, (call, sid) in enumerate(assign(plan, trace)):
        if sid is None:
            extra.append(call["step"])
        else:
            first.setdefault(sid, pos)
    labels, latest = {}, -1
    for step in plan["steps"]:
        sid = step["id"]
        if sid not in first:
            labels[sid] = "skipped"
        elif first[sid] < latest:
            labels[sid] = "reordered"   # it ran before a step that comes earlier in the plan
        else:
            labels[sid] = "executed"
        latest = max(latest, first.get(sid, -1))
    return {"steps": labels, "extra": extra}
