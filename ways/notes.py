"""Way 6, notes: the planned way with a progress file and a compaction. This file is yours.

Assignment 2, Part G. Your planned way with the second pass off, plus two things,
so that Part F's configurations stay as they were (checks/FORMATS.md, section 10):

    python run.py --way notes --notes off --issues 4203,4204,4206 --runs 2
    python run.py --way notes --notes on --issues 4203,4204,4206 --runs 2

  1. A progress file, ctx.progress_path (runs/<run_id>.progress.json): JSON outside
     the conversation, written after every step, holding at least
       "steps_done"  the ids of the steps that have run, in order
       "paths"       every path a search returned or a read read, in order
     and never a search term: terms come from the issue's text. Say in the file what
     it records (an "about" line, or labelled keys): a bare list of paths can be read
     as a to-do list. With ctx.notes == "on", every executor call and the questions'
     call after its first write show the model the file's text, exactly as written
     (not the planner, not the reviser). With "off", it is still written, and never
     shown.
  2. A history and a compaction: every model call sees the run's history so far,
     its step results oldest first. Once that history passes COMPACT_AT tokens, the
     oldest entries become a model-written summary and the recent ones stay as
     they were. Write ctx.trace.event("compaction", threshold=COMPACT_AT, tokens=...)
     each time. It runs whatever ctx.notes says. A low threshold is a test setting
     here, not advice.

Then, at the end of the run, however it ended (comment posted, escalated, halted),
one model call asks six closed questions, each "Has this run seen `<path>`: did a
search return it, or did you read it?", and returns six answers, yes or no: about
the three earliest paths the run saw (its tool_call lines returned them; all of
them, if it saw fewer), and paths it did not see for the rest. If the run has not
compacted yet, compact its history once first. Write each answer as
ctx.trace.event("question", path=..., answer=...), the six together.

Part G's 12 runs are the ones on issues 4203, 4204 and 4206; a notes run on a
development issue is development, and reports/LAB_02.json's context leaves it out.
"""

from __future__ import annotations

import json
import random
import re

from harness import ROOT
from ways.planned import FILL, MAX_REPLANS, RESULT_CHARS, Executor, full_searches, get_plan

COMPACT_AT = 400       # tokens (chars / 4). read_issue plus one search is past it, so runs compact mid-run
KEEP_RECENT = 1        # how many of the newest history entries stay as they are after a compaction
QUESTIONS = 6
SEEN_ASKED = 3         # ask about the 3 earliest paths the run saw, the rest are paths it did not see
ABOUT = ("RepoMind's progress file for this run. steps_done: plan step ids that have run, in order. "
         "paths: every file path a search returned or a read read in this run, in order. "
         "It is a record of what was already seen, not a to-do list.")


def tokens(text: str) -> int:
    return len(text) // 4


class NotesExecutor(Executor):
    """The planned executor plus a progress file, a history that gets compacted, and
    six questions at the end. It only overrides hooks, ways/planned.py stays the same."""

    def __init__(self, ctx, plan: dict):
        super().__init__(ctx, plan)
        self.history = []        # one text entry per step result, oldest first
        self.summary = None      # the model-written summary of the compacted entries
        self.compactions = 0
        self.asked = False
        self.k = 0               # how many entries of self.seen are already in the history

    # ---- the progress file ----
    def progress(self) -> dict:
        steps_done, paths = [], []
        for r in self.step_results:
            if not steps_done or steps_done[-1] != r["id"]:
                steps_done.append(r["id"])
            paths += [p for p in r["paths"] if p not in paths]
        # only ids and paths, never args: a search query comes from the issue text
        return {"about": ABOUT, "steps_done": steps_done, "paths": paths}

    def write_progress(self) -> None:
        self.ctx.progress_path.write_text(json.dumps(self.progress(), indent=1))

    def progress_text(self) -> str | None:
        """The file's text exactly as written, only with --notes on (and only once it exists)."""
        if self.ctx.notes != "on" or not self.ctx.progress_path.exists():
            return None
        return self.ctx.progress_path.read_text()

    # ---- the history and the compaction ----
    def history_text(self) -> str:
        parts = [f"Summary of the earlier steps: {self.summary}"] if self.summary is not None else []
        return "\n".join(parts + self.history)

    def after_step(self, step: dict) -> None:
        for sid, tool, args, text in self.seen[self.k:]:
            status = next((r["status"] for r in reversed(self.step_results) if r["id"] == sid), "?")
            self.history.append(f"--- step {sid}, {tool} {json.dumps(args)} ({status})\n{text[:RESULT_CHARS]}")
        self.k = len(self.seen)
        self.write_progress()
        # after the questions nothing reads the history again, so no point compacting
        if not self.asked and tokens(self.history_text()) > COMPACT_AT:
            self.compact()

    def compact(self) -> None:
        """The oldest entries become one model-written summary, the newest KEEP_RECENT stay."""
        size = tokens(self.history_text())
        old = self.history[:-KEEP_RECENT] if len(self.history) > KEEP_RECENT else []
        if old or self.summary is None:
            text = "\n".join(([f"Earlier summary: {self.summary}"] if self.summary else []) + old)
            self.n += 1
            response = self.ctx.client.create(
                system=self.ctx.prompt("compact.md"),
                messages=[{"role": "user", "content": "Summarize these step results:\n" + (text or "(no steps ran)")}],
                tools=[])
            self.ctx.trace.model_call(self.n, response, purpose=None)
            self.summary = response.text or "(empty summary)"
            self.history = self.history[len(old):]
        self.compactions += 1
        self.ctx.trace.event("compaction", threshold=COMPACT_AT, tokens=size, kept=len(self.history))

    def build_messages(self, step: dict, args: dict, problem: str | None) -> list[dict]:
        lines = [f"Issue #{self.ctx.issue_number}. The plan:"]
        lines += [f"{s['id']}. {s['action']} ({s['tool']})" for s in self.plan["steps"]]
        lines.append("\nThe run so far (oldest first):\n" + (self.history_text() or "(nothing yet)"))
        notes = self.progress_text()
        if notes:
            lines.append(f"\nThe progress file ({self.ctx.progress_path.name}):\n{notes}")
        lines.append(f"\nNow step {step['id']}: {step['action']}. Call {step['tool']} with these "
                     f"arguments, filling in each \"{FILL}\": {json.dumps(args)}")
        if problem:
            lines.append(f"Your last try did not work: {problem}.")
        return [{"role": "user", "content": "\n".join(lines)}]

    # ---- the questions ----
    def run_step(self, step: dict) -> bool:
        if step["tool"] == "post_comment" and not self.asked:
            self.ask()       # just before the comment is drafted
        return super().run_step(step)

    def before_comment(self, step: dict, args: dict) -> dict | None:
        return args          # the second pass is off in this way

    def run(self) -> bool:
        # an escalation ends the run inside Executor.run, so ask right before its trace.end
        real_end = self.ctx.trace.end

        def end(reason, detail=""):
            if not self.asked:
                self.ask()
            real_end(reason, detail)

        self.ctx.trace.end = end
        try:
            ok = super().run()
        finally:
            del self.ctx.trace.end
        if not self.asked:
            self.ask()
        return ok

    def question_paths(self) -> list[str]:
        seen = self.progress()["paths"]
        asked = seen[:SEEN_ASKED]
        repo = ROOT / "fixtures" / "repo"
        unseen = sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file()
                        and str(p.relative_to(repo)) not in seen)
        rng = random.Random(self.ctx.run_id)   # same run id -> same picks
        paths = asked + rng.sample(unseen, QUESTIONS - len(asked))
        rng.shuffle(paths)                      # so "yes" is not always first
        return paths

    def ask(self) -> None:
        """Six yes/no questions in one call. Compact first if the run has not compacted yet."""
        self.asked = True
        if not self.compactions:
            self.compact()
        paths = self.question_paths()
        lines = ["The run so far (oldest first):\n" + (self.history_text() or "(nothing yet)")]
        notes = self.progress_text()
        if notes:
            lines.append(f"\nThe progress file ({self.ctx.progress_path.name}):\n{notes}")
        lines.append("\nAnswer each question with yes or no:")
        lines += [f"{i}. Has this run seen `{p}`: did a search return it, or did you read it?"
                  for i, p in enumerate(paths, 1)]
        lines.append("Reply with exactly six lines, like \"1: yes\".")
        self.n += 1
        response = self.ctx.client.create(system=self.ctx.prompt("questions.md"),
                                          messages=[{"role": "user", "content": "\n".join(lines)}], tools=[])
        self.ctx.trace.model_call(self.n, response, purpose=None)
        answers = parse_answers(response.text)
        for i, path in enumerate(paths, 1):
            self.ctx.trace.event("question", path=path, answer=answers.get(i, "no"))


def parse_answers(text: str) -> dict[int, str]:
    """Read "1: yes" style lines; if the model only wrote yes/no words, take them in order."""
    found = {int(n): a.lower() for n, a in re.findall(r"(\d)\W{0,3}\s*(yes|no)\b", text or "", re.IGNORECASE)}
    if not found:
        words = re.findall(r"\b(yes|no)\b", (text or "").lower())
        found = {i: w for i, w in enumerate(words, 1)}
    return found


def run(ctx) -> None:
    plan = get_plan(ctx)
    if plan is None:
        # no plan, but every run still compacts and asks before it ends
        NotesExecutor(ctx, {"steps": []}).ask()
        ctx.trace.end("escalated", f"every plan was rejected: {MAX_REPLANS + 1} planner calls")
        return
    for step in plan["steps"]:
        step["status"] = "pending"
    full_searches(plan)
    ctx.trace.plan(1, plan)
    NotesExecutor(ctx, plan).run()
