"""Assignment 2, Parts A and C: the planner and the reviser. This file is yours.

The contract (checks/FORMATS.md, section 10). The checker calls both functions
directly, with a scripted model that keeps a copy of every request, so keep these
exact signatures. You may add keyword arguments with defaults; the checker passes
only these.

    make_plan(client, issue_number, tool_schemas, trace=None) -> dict
    revise_plan(client, plan, step_results, trace=None) -> dict

    client        harness.client.ModelClient: client.create(system=, messages=, tools=)
    issue_number  the issue to triage, an int
    tool_schemas  the tool definitions, as ctx.tools.schemas gives them
    plan          the active plan, its steps' statuses brought up to date
    step_results  one entry per step that has run (section 10.3)
    trace         a harness.trace.TraceWriter, or None. When you get one, trace your
                  model call: trace.model_call(0, response, purpose="plan") in
                  make_plan, purpose="revise" in revise_plan
    returns       a plan (section 10.1): {"meta": {...}, "steps": [...]}

make_plan writes the plan and stops (Lecture 2, slide 15). Its request carries the
task, the issue number and the tool list, and nothing from the issue: not its body,
not its title. The checker looks for each issue's sentinel string in every request.
The usual way to get the plan back as data: offer the model one tool whose
input_schema is agent/plan_schema.json, and read the plan from its call. Mark an
argument that waits on an earlier step's result "$fill"; the executor fills it in.

revise_plan repairs before it replaces: it never changes a step whose status is
done; it may change a step that failed, to retry it, and any step not yet run. A
step whose step result says "done" comes back unchanged, every field but status,
whatever the model wrote. Its request carries structured step results only (status, counts, paths,
error codes): never the issue's text, and never a step result's `args`, which the
executor may have filled in from the issue.

Keep your prompts in prompts/.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "agent" / "plan_schema.json"
PLANNER_PROMPT = "prompts/planner.md"
PLAN_TOOL = "submit_plan"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def plan_tool() -> dict:
    """The only tool the planner gets. The plan comes back as the input of this tool call."""
    return {
        "name": PLAN_TOOL,
        "description": "Submit the plan: the list of steps, in the order they run.",
        "input_schema": load_schema(),
        "strict": True,
    }


def describe_tools(tool_schemas: list[dict]) -> str:
    """Turn the real tools into text, so the planner can plan with them but not call them."""
    return "\n".join(
        f"- {t['name']}: {t.get('description', '').strip()}\n"
        f"  arguments: {json.dumps(t.get('input_schema') or {})}"
        for t in tool_schemas
    )


def make_plan(client, issue_number: int, tool_schemas: list[dict], trace=None,
              feedback: list[str] | None = None) -> dict:
    """Ask the model for a plan. It only gets the task, the issue number and the tools,
    not the issue text. When we re-plan, feedback says what was wrong with the last plan."""
    user = (f"Plan the triage of issue #{issue_number}.\n\n"
            f"The tools your plan may use:\n{describe_tools(tool_schemas)}")
    if feedback:
        user += ("\n\nYour last plan was rejected. Write a new one that fixes these problems:\n"
                 + "\n".join(f"- {line}" for line in feedback))
    response = client.create(system=(ROOT / PLANNER_PROMPT).read_text(),
                             messages=[{"role": "user", "content": user}],
                             tools=[plan_tool()])
    if trace is not None:
        trace.model_call(0, response, purpose="plan")

    meta = {"model": response.model, "prompt": PLANNER_PROMPT,
            "tools": [t["name"] for t in tool_schemas]}
    call = next((c for c in response.tool_calls if c.name == PLAN_TOOL), None)
    if call is None:
        # model answered with text instead of a plan -> return empty steps so check 4 rejects it
        return {"meta": {**meta, "error": f"no {PLAN_TOOL} call ({response.stop_reason})"}, "steps": []}
    plan = dict(call.input)
    plan["meta"] = meta
    return plan


REVISER_PROMPT = "prompts/reviser.md"
RESULT_FIELDS = ("id", "tool", "status", "count", "paths", "error_code")   # no "args": they can carry issue text


def _without_status(step) -> dict:
    return {k: v for k, v in (step or {}).items() if k != "status"}


def revise_plan(client, plan: dict, step_results: list[dict], trace=None,
                feedback: list[str] | None = None) -> dict:
    """Ask the model for the next version of the plan. It gets the plan and the
    step results (status, count, paths, error code), never the issue or the args.
    Steps that are done always come back exactly as they were. feedback says what
    was wrong with the last revision, if it was rejected."""
    steps = [s for s in plan.get("steps") or [] if isinstance(s, dict)]
    results = [{k: r.get(k) for k in RESULT_FIELDS} for r in step_results]
    # show the plan as plain lines, not json.dumps: otherwise args_json shows up with
    # backslashes ({\"query\": ...}) and the model copies them
    plan_lines = [f"- id: {s.get('id')} | action: {s.get('action')} | tool: {s.get('tool')} | "
                  f"args_json: {s.get('args_json')} | depends_on: {s.get('depends_on')} | "
                  f"expected_result: {s.get('expected_result')} | status: {s.get('status')}" for s in steps]
    user = ("The current plan:\n" + "\n".join(plan_lines)
            + "\n\nWhat the steps that ran returned:\n" + json.dumps(results, indent=1)
            + "\n\nThe tools a step may use: " + ", ".join((plan.get("meta") or {}).get("tools") or []))
    if feedback:
        user += ("\n\nYour last revision was rejected. Write a new one that fixes these problems:\n"
                 + "\n".join(f"- {line}" for line in feedback))
    response = client.create(system=(ROOT / REVISER_PROMPT).read_text(),
                             messages=[{"role": "user", "content": user}],
                             tools=[plan_tool()])
    if trace is not None:
        trace.model_call(0, response, purpose="revise")

    meta = {**(plan.get("meta") or {}), "model": response.model, "prompt": REVISER_PROMPT}
    meta.pop("error", None)
    call = next((c for c in response.tool_calls if c.name == PLAN_TOOL), None)
    if call is None:
        # no plan came back -> empty steps, so the validator rejects this revision
        return {"meta": {**meta, "error": f"no {PLAN_TOOL} call ({response.stop_reason})"}, "steps": []}

    revised = dict(call.input)
    new_steps = revised.get("steps") if isinstance(revised.get("steps"), list) else []
    # done steps: put the old ones back no matter what the model wrote. They are always
    # at the front, because the checkpoint is right after the step that just ran.
    done = [s for s in steps if s.get("status") == "done"]
    done_ids = {str(s.get("id")) for s in done}
    old = {str(s.get("id")): s for s in steps}
    rest = []
    for s in new_steps:
        if isinstance(s, dict):
            if str(s.get("id")) in done_ids:
                continue  # already put back above
            s = dict(s)
            before = old.get(str(s.get("id")))
            # an unchanged step keeps its status (so a failed one is not re-run as is),
            # a new or changed step is pending
            unchanged = before is not None and _without_status(before) == _without_status(s)
            s["status"] = before.get("status", "pending") if unchanged else "pending"
        rest.append(s)  # anything broken stays in, so the schema check catches it
    revised["steps"] = [dict(s) for s in done] + rest
    revised["meta"] = meta
    return revised
