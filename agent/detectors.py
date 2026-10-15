"""Assignment 2, Part E: three detectors, and Lecture 2's ladder. This file is yours.

Three of the four plan failures have a detector of a few lines over a trace
(Lecture 2, slides 45 to 49); the validator is the fourth. The checker calls these
on fixtures/traces/, so keep these exact signatures (checks/FORMATS.md, section
10.2):

    repeat_check(trace) -> list[list]
        Circular reasoning. Exact repeats: two tool_call lines with the same tool
        and the same arguments. Returns each group of repeats as the step numbers
        of its tool_call lines, e.g. [[1, 3], [2, 4]].

    orphan_check(plan) -> list
        Hallucinated steps. The ids of steps, other than the last, whose result no
        later step uses: no later step lists them in depends_on.

    expected_result_check(plan, trace) -> list
        Premature stopping, in part. The ids of plan steps that did not run and
        return their result. It catches skipped steps, not a plan too short.

    ladder(plan, trace) -> {"q1": {"fired": bool, "ids": [...]}, "q2": ..., "q3": ...}
        Lecture 2's questions 1 to 3 (slide 50), in order: did any two steps do
        the same thing; is any step's result never used; did every planned step
        execute. "ids" says what fired: groups of step numbers for q1, step ids
        (or, from a trace, step numbers) for q2 and q3. Question 4 needs
        judgment, so it goes in your report, not here.

    plan    a plan (section 10.1)
    trace   a list of trace lines, as harness.trace.read_trace returns them

A plan with no depends_on cannot answer question 2 from the plan. Answer it from
the trace, and say how in your report.
"""

from __future__ import annotations

import json

from agent.diff import match_calls, tool_calls


def repeat_check(trace: list[dict]) -> list[list]:
    """Circular reasoning: tool calls with the same tool and the exact same arguments."""
    groups = {}
    for call in tool_calls(trace):
        key = (call["name"], json.dumps(call.get("input"), sort_keys=True))
        groups.setdefault(key, []).append(call["step"])
    return [steps for steps in groups.values() if len(steps) > 1]


def orphan_check(plan: dict) -> list:
    """Hallucinated steps: steps (not the last) that no later step lists in depends_on."""
    steps = plan["steps"]
    orphans = []
    for i, step in enumerate(steps[:-1]):
        used = any(str(step["id"]) in {str(d) for d in later.get("depends_on") or []} for later in steps[i + 1:])
        if not used:
            orphans.append(step["id"])
    return orphans


def expected_result_check(plan: dict, trace: list[dict]) -> list:
    """Premature stopping (partly): plan steps with no matching call, or whose call was an error.
    A step with no tool (like "identify the bug") never returns anything, so it counts too."""
    matched = match_calls(plan, trace)
    return [s["id"] for s in plan["steps"]
            if s["id"] not in matched or matched[s["id"]].get("is_error")]


def unused_from_trace(trace: list[dict]) -> list:
    """Question 2 when the plan has no depends_on, so it has to be a judgment from the trace.
    My rule: if the exact same call is made again later, the agent did not use what it
    got the first time (it went and asked again). Returns those first calls' step numbers."""
    return sorted(group[i] for group in repeat_check(trace) for i in range(len(group) - 1))


def ladder(plan: dict, trace: list[dict]) -> dict:
    """Lecture 2's questions 1 to 3, in order."""
    repeats = repeat_check(trace)
    # q2 from the dependency graph if the plan has one, otherwise from the trace
    has_deps = any("depends_on" in s for s in plan["steps"])
    unused = orphan_check(plan) if has_deps else unused_from_trace(trace)
    missing = expected_result_check(plan, trace)
    return {"q1": {"fired": bool(repeats), "ids": repeats},
            "q2": {"fired": bool(unused), "ids": unused},
            "q3": {"fired": bool(missing), "ids": missing}}


if __name__ == "__main__":
    from pathlib import Path

    from agent.diff import diff
    from harness.trace import read_trace

    root = Path(__file__).resolve().parent.parent / "fixtures" / "traces"
    for name in ("looping", "s49"):
        plan = json.loads((root / name / "plan.json").read_text())
        trace = read_trace(root / name / "trace.jsonl")
        print(name, json.dumps(ladder(plan, trace)))
        print(name, "diff", json.dumps(diff(plan, trace)))
