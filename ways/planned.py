"""Way 5, planned: RepoMind with a plan it cannot quietly abandon. This file is yours.

Assignment 2, Parts B, C and D. Like every way, run(ctx) -> None (see ways/__init__.py):

    python run.py --way planned --set dev
    python run.py --way planned --critique self --set dev      # or external; off is the default

The run, in order (checks/FORMATS.md, section 10):

  1. Plan: agent.plan.make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas,
     trace=ctx.trace). The planner never sees the issue.
  2. Check, then validate. First the plan against agent/plan_schema.json, with
     harness.schema.errors (a vendor may accept "strict" and not enforce it), and
     that its last step is post_comment (a plan without one passes the four checks
     and posts nothing); a plan that breaks either writes
     ctx.trace.event("schema", errors=...). Then agent.validator.validate(plan). A
     plan that fails any of these is rejected and re-planned, at most MAX_REPLANS
     times; then the run escalates: ctx.trace.end("escalated", detail), no tool
     call, nothing posted.
  3. Log: ctx.trace.plan(1, plan), before the first tool call.
  4. Execute the steps in order, each tool call through ctx.call_tool, naming the
     plan step it runs for: ctx.call_tool(n, tool, args, plan_step=step["id"]). Where a step
     has arguments marked "$fill", one model call fills them in: offer it that
     step's tool alone, and trace it with ctx.trace.model_call(n,
     response, purpose="execute"). Act on the tool calls a response holds,
     whatever its stop_reason says. The executor may run, retry or halt a step; it
     may not add, drop or reorder one. A call that is not the current step's tool
     never runs: refuse it and write ctx.trace.event("off_plan", step=..., tool=...).
  5. Part C: revise only at one checkpoint, after a search step: the first search
     step, and any search step a revision added or changed, until MAX_REVISIONS is
     spent; and only when the check there fails (a check in code, not in a prompt,
     such as "the search returned at least one source file, not only documentation
     or translations"): revise_plan, which never changes a step that is done but may
     change a failed one to retry it, then check the new version against the schema
     and validate it, write
     ctx.trace.event("revision", checkpoint=..., version=..., valid=...), and log a
     valid one with ctx.trace.plan(version, plan). A revision that fails the
     validator never runs, and counts against the bound.
  6. Part D: before post_comment, the second pass, in the mode ctx.critique names
     (off, self or external), at most MAX_ROUNDS rounds. A round checks the
     current draft (agent.critique.verify, or agent.critique.self_critique) and
     writes ctx.trace.event("critique", round=..., mode=..., result="pass" or
     "fail", reason=..., changed=..., draft=...). A draft that fails, with a round
     left, is rewritten by one model call given the reason, traced with
     purpose="critique", and the next round checks the new draft. A draft still
     failing in the last round is held: not posted, and the run ends escalated.
     Nothing is posted until the last round, and a run posts one comment at most.

The comment is Assignment 1's: two lines, label: and file: (section 1). A run that
finishes its plan can leave its ending to the runner, which writes completed.

Part G's ways/notes.py extends this way without changing it, so build this one with
hooks it can override: for example an executor class whose methods run after every
step, build what each model call sees, and run before the comment.

The checker reads MAX_REPLANS, MAX_REVISIONS and MAX_ROUNDS from this module, and
runs this way against scripted models (harness/scripted.py). You can run them too,
free:

    python run.py --way planned --backend harness.scripted:AlwaysInvalidPlanner --set dev
    python run.py --way planned --backend harness.scripted:OffPlanExecutor --set dev
    python run.py --way planned --critique external --backend harness.scripted:DraftNamesUnseenFile --set dev
"""

from __future__ import annotations

import json

from agent.critique import describe_evidence, self_critique, verify
from agent.plan import load_schema, make_plan, revise_plan
from agent.validator import validate
from harness.formats import FILL, result_paths
from harness.schema import errors as schema_errors

MAX_REPLANS = 2        # usually one retry with the error list fixes it, after 3 bad plans I give up
MAX_REVISIONS = 2      # one new search, and one more if that one also finds no source file; more is just guessing
MAX_ROUNDS = 2         # check, rewrite once, check again; a second rewrite rarely fixes what the first didn't

MAX_STEP_TRIES = 2     # a step can be tried twice (one retry), then the run stops
RESULT_CHARS = 6000    # how much of each earlier result the executor model gets to see
SEARCH_HITS = 20       # search_repo's max. docs come first in path order, so ask for all of them

CHECKS = {1: "its tool is not in the tool list",
          2: "depends_on names a step that is not before it",
          3: "it has no tool or no expected_result",
          4: "it has no steps, or too many"}


def schema_problems(plan: dict) -> list[str]:
    """Check the plan against my schema (Gemini may ignore strict), that args_json
    is real JSON, and that the plan ends with post_comment."""
    problems = schema_errors({k: v for k, v in plan.items() if k != "meta"}, load_schema())
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    for step in steps:
        args = step.get("args_json") if isinstance(step, dict) else None
        if isinstance(args, str):
            try:
                ok = isinstance(json.loads(args), dict)
            except ValueError:
                ok = False
            if not ok:
                problems.append(f"step {step.get('id')}: args_json is not a JSON object")
    if steps and not (isinstance(steps[-1], dict) and steps[-1].get("tool") == "post_comment"):
        problems.append("the last step is not post_comment")
    return problems


def get_plan(ctx) -> dict | None:
    """Make a plan and check it. If it is bad, try again (max MAX_REPLANS times).
    Returns None if every plan was bad."""
    feedback = None
    for _ in range(MAX_REPLANS + 1):
        plan = make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas, trace=ctx.trace, feedback=feedback)
        problems = schema_problems(plan)
        if problems:
            ctx.trace.event("schema", errors=problems)
        checked = validate(plan)
        if not problems and checked["valid"]:
            return plan
        feedback = rejection_feedback(plan, problems, checked)
    return None


def rejection_feedback(plan: dict, problems: list[str], checked: dict) -> list[str]:
    """What was wrong with a rejected plan, for the planner or reviser to fix.
    Only schema errors and failed checks, never anything from the issue."""
    feedback = problems + [f"step {f['step']}: {CHECKS[f['check']]}" if f["step"] is not None
                           else f"the plan: {CHECKS[f['check']]}" for f in checked["failures"]]
    if plan.get("meta", {}).get("error"):
        feedback.append(f"{plan['meta']['error']}: answer by calling submit_plan")
    return feedback


def full_searches(plan: dict) -> None:
    """Make every search ask for all 20 hits, before the plan is logged. With fewer, a
    vague word fills the list with CHANGELOG/README/docs and never reaches src/."""
    for step in plan["steps"]:
        if step["tool"] == "search_repo":
            args = json.loads(step["args_json"])
            args["max_results"] = SEARCH_HITS
            step["args_json"] = json.dumps(args)


def source_found(paths: list[str]) -> bool:
    """The checkpoint's check: did the search find a real source file (a .ts or .tsx
    under src/, not a translation file)? Docs, README or CHANGELOG alone don't count."""
    return any(p.startswith("src/") and p.endswith((".ts", ".tsx")) and not p.startswith("src/i18n/")
               for p in paths)


def _without_status(step) -> dict:
    return {k: v for k, v in (step or {}).items() if k != "status"}


class Executor:
    """Runs the plan one step at a time. It can run, retry or halt a step, but it
    can never add, drop or reorder one. Parts C, D and G hook into the methods
    after_step, before_comment and build_messages."""

    def __init__(self, ctx, plan: dict):
        self.ctx = ctx
        self.plan = plan
        self.version = 1
        self.revisions = 0         # revision attempts so far (good and bad)
        self.n = 0                 # number of my model calls so far (the trace "step")
        self.step_results = []     # one entry per step that ran (FORMATS 10.3)
        self.seen = []             # (step, tool, args, result text) for the executor model
        # the checkpoint: after the first search (later also after searches a revision adds or changes)
        first_search = next((s["id"] for s in plan["steps"] if s["tool"] == "search_repo"), None)
        self.checkpoints = {first_search} if first_search is not None else set()
        self.held = None           # Part D: why the draft was not posted, if it was held

    def run(self) -> bool:
        """Run the pending steps in order. Returns False if a step could not run (run escalated)."""
        while True:
            # always the first pending step of the current plan version, so a revision
            # can change what comes next, but the executor itself never skips or reorders
            step = next((s for s in self.plan["steps"] if s.get("status") == "pending"), None)
            if step is None:
                return True
            ok = self.run_step(step)
            self.after_step(step)
            # the checkpoint only counts if the search really ran (so its result is the last one)
            ran = bool(self.step_results) and self.step_results[-1]["id"] == step["id"]
            if step["id"] in self.checkpoints and ran:
                self.checkpoints.discard(step["id"])
                if self.checkpoint(step):
                    continue       # new plan version, go on with its first pending step
            # a search that ran but found nothing does not stop the run, the next steps still try.
            # a step that could not run at all (e.g. only off-plan calls) stops it
            if not ok and not (step["tool"] == "search_repo" and ran):
                self.ctx.trace.end("escalated", self.held or f"step {step['id']} ({step['tool']}) could not run")
                return False

    def checkpoint(self, step: dict) -> bool:
        """The only place the plan can change. Returns True if a new version was accepted."""
        result = self.step_results[-1]
        if source_found(result["paths"]) or self.revisions >= MAX_REVISIONS:
            return False
        # the search did not return what it should, so it counts as failed for the reviser
        result.update(status="failed", error_code=result["error_code"] or "no_source_file")
        step["status"] = "failed"

        # ask the reviser; if its plan is rejected, try again here with the errors,
        # as long as revisions are left (every try counts, good or bad)
        version = self.version + 1
        feedback = None
        while True:
            self.revisions += 1
            new = revise_plan(self.ctx.client, self.plan, self.step_results, trace=self.ctx.trace,
                              feedback=feedback)
            problems = schema_problems(new)
            if problems:
                self.ctx.trace.event("schema", errors=problems, version=version)
            checked = validate(new)
            valid = not problems and checked["valid"]
            self.ctx.trace.event("revision", checkpoint=f"after search step {step['id']}", version=version,
                                 valid=valid, failures=checked["failures"])
            if valid:
                break
            if self.revisions >= MAX_REVISIONS:
                return False  # a bad revision never runs, the old plan goes on
            feedback = rejection_feedback(new, problems, checked)

        # searches the revision added or changed get a checkpoint too
        old = {s["id"]: s for s in self.plan["steps"]}
        for s in new["steps"]:
            if s["tool"] == "search_repo" and s["status"] == "pending" and \
                    _without_status(old.get(s["id"])) != _without_status(s):
                self.checkpoints.add(s["id"])
        full_searches(new)
        self.ctx.trace.plan(version, new)
        self.plan, self.version = new, version
        return True

    def run_step(self, step: dict) -> bool:
        problem = None
        # a search at the checkpoint gets one try: if it is weak, the reviser decides what next.
        # post_comment also gets one try, so the second pass never runs twice
        tries = 1 if step["id"] in self.checkpoints or step["tool"] == "post_comment" else MAX_STEP_TRIES
        for _ in range(tries):
            args = json.loads(step["args_json"])
            if FILL in args.values():
                args, problem = self.fill(step, args, problem)
                if args is None:
                    continue
            if step["tool"] == "post_comment":
                args = self.before_comment(step, args)
                if args is None:
                    step["status"] = "failed"
                    return False
            result = self.ctx.call_tool(self.n, step["tool"], args, plan_step=step["id"])
            entry = self.record(step, args, result)
            if entry["status"] == "done":
                return True
            problem = f"the last try returned {entry['error_code']}: {result.content[:200]}"
        step["status"] = "failed"   # so the loop in run() never picks this step again
        return False

    def fill(self, step: dict, args: dict, problem: str | None):
        """One model call to fill in the "$fill" arguments. The model only gets this
        step's tool, and any call to another tool is refused and logged as off_plan."""
        tool_schema = next(t for t in self.ctx.tools.schemas if t["name"] == step["tool"])
        self.n += 1
        response = self.ctx.client.create(system=self.ctx.prompt("executor.md"),
                                          messages=self.build_messages(step, args, problem),
                                          tools=[tool_schema])
        self.ctx.trace.model_call(self.n, response, purpose="execute")

        # look at the tool calls no matter what stop_reason says
        for call in response.tool_calls:
            if call.name != step["tool"]:
                self.ctx.trace.event("off_plan", step=step["id"], tool=call.name, input=call.input)
                continue
            # only the "$fill" arguments come from the model, the rest stay as the plan says
            filled = {k: (call.input.get(k) if v == FILL else v) for k, v in args.items()}
            missing = [k for k, v in filled.items() if v in (None, "", FILL)]
            if not missing:
                return filled, None
            return None, f"you left {', '.join(missing)} empty"
        if response.tool_calls:
            return None, f"you called a tool that is not {step['tool']}; that call was refused"
        return None, f"you did not call {step['tool']}"

    def build_messages(self, step: dict, args: dict, problem: str | None) -> list[dict]:
        lines = [f"Issue #{self.ctx.issue_number}. The plan:"]
        lines += [f"{s['id']}. {s['action']} ({s['tool']})" for s in self.plan["steps"]]
        lines.append("\nWhat the earlier steps returned:")
        for sid, tool, a, text in self.seen:
            lines.append(f"--- step {sid}, {tool} {json.dumps(a)}\n{text[:RESULT_CHARS]}")
        lines.append(f"\nNow step {step['id']}: {step['action']}. Call {step['tool']} with these "
                     f"arguments, filling in each \"{FILL}\": {json.dumps(args)}")
        if problem:
            lines.append(f"Your last try did not work: {problem}.")
        return [{"role": "user", "content": "\n".join(lines)}]

    def record(self, step: dict, args: dict, result) -> dict:
        """Write the step result (10.3) and update the step's status."""
        paths = result_paths(step["tool"], args, result.content, result.is_error)
        count = len(paths) if step["tool"] == "search_repo" else None
        if result.is_error:
            status, code = "failed", "tool_error" if "no such file" not in result.content else "not_found"
        elif step["tool"] == "search_repo" and not paths:
            status, code = "failed", "no_matches"
        else:
            status, code = "done", None
        entry = {"id": step["id"], "tool": step["tool"], "status": status, "count": count,
                 "paths": paths, "error_code": code, "args": args}
        self.step_results.append(entry)
        step["status"] = status
        self.seen.append((step["id"], step["tool"], args, result.content))
        return entry

    # hooks for later parts
    def after_step(self, step: dict) -> None:
        """Runs after every step. Empty for now (Part G uses it)."""

    def before_comment(self, step: dict, args: dict) -> dict | None:
        """Part D: the second pass, before anything is posted. Check the draft (verify in
        external mode, self_critique in self mode); if it fails and a round is left,
        rewrite it with the reason and check again. None means hold the draft (don't post)."""
        mode = self.ctx.critique
        if mode == "off":
            return args
        draft, changed = args.get("body") or "", False
        for rnd in range(1, MAX_ROUNDS + 1):
            if mode == "external":
                checked = verify(draft, self.ctx)
                passed, reason = checked["passed"], "; ".join(checked["reasons"]) or None
            else:
                self.n += 1
                checked = self_critique(self.ctx.client, draft, self.evidence(), trace=self.ctx.trace, step=self.n)
                passed, reason = checked["decision"] == "accept", checked["reason"]
            self.ctx.trace.event("critique", round=rnd, mode=mode, result="pass" if passed else "fail",
                                 reason=reason, changed=changed, draft=draft)
            if passed:
                return {**args, "body": draft}
            if rnd < MAX_ROUNDS:
                new = self.rewrite(draft, reason)
                changed = new.strip() != draft.strip()   # same text back = no change
                draft = new
        self.held = f"the draft still failed the {mode} second pass after {MAX_ROUNDS} rounds, not posted"
        return None

    def evidence(self) -> dict:
        """What the critic and the rewrite see: the issue and what each step returned."""
        issue = next((text for _, tool, _, text in self.seen if tool == "read_issue"), "")
        results = {r["id"]: r["status"] for r in self.step_results}
        return {"issue": issue,
                "steps": [{"id": sid, "tool": tool, "args": a, "status": results.get(sid), "result": text}
                          for sid, tool, a, text in self.seen if tool != "read_issue"]}

    def rewrite(self, draft: str, reason: str | None) -> str:
        """One model call that fixes the draft, given why it failed. No tools, just text back."""
        self.n += 1
        user = (describe_evidence(self.evidence()) + "\n\nThe draft comment:\n" + draft
                + f"\n\nWhy it was not accepted: {reason}\n\nRewrite the comment.")
        response = self.ctx.client.create(system=self.ctx.prompt("rewrite.md"),
                                          messages=[{"role": "user", "content": user}], tools=[])
        self.ctx.trace.model_call(self.n, response, purpose="critique")
        # models like to wrap it in ``` fences, take those off
        text = response.text.strip().removeprefix("```text").removeprefix("```").removesuffix("```").strip()
        return text or draft


def run(ctx) -> None:
    # step 1-2: get a plan that passes all the checks (the planner never sees the issue)
    plan = get_plan(ctx)
    if plan is None:
        ctx.trace.end("escalated", f"every plan was rejected: {MAX_REPLANS + 1} planner calls")
        return

    # step 3: log the plan before any tool runs (meta has the model, prompt and tools)
    for step in plan["steps"]:
        step["status"] = "pending"   # nothing has run yet, whatever the model wrote
    full_searches(plan)
    ctx.trace.plan(1, plan)

    # step 4: run the steps in order; if one can't run, the executor escalates
    Executor(ctx, plan).run()
