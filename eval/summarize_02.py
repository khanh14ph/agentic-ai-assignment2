"""Assignment 2, Part F: builds the results table from runs/ and the tracker.

Writes `results` and `context` in reports/LAB_02.json and prints the Part F and
Part G tables as markdown.

    python -m eval.summarize_02

Only finished runs of this lab on the six eval issues count, no scripted backends.
A run passes by eval/check_triage.check (the comment with its run_id), never by
what the agent says.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import yaml

from agent.detectors import repeat_check
from eval.check_triage import check
from harness import ROOT
from harness.formats import UNFINISHED, normalize_path, parse_triage_comment
from harness.trace import read_trace
from harness.tracker import Tracker

CONFIGS = ("agent", "planned_off", "planned_self", "planned_external")


def config_of(start: dict) -> str | None:
    if start.get("way") == "agent":
        return "agent"
    if start.get("way") == "planned":
        return f"planned_{start.get('critique') or 'off'}"
    return None


def load_runs(answers: dict) -> dict[str, list[list[dict]]]:
    runs = defaultdict(list)
    for path in sorted((ROOT / "runs").glob("*.jsonl")):
        if path.name.startswith("_"):
            continue
        records = read_trace(path)
        start, end = records[0], records[-1]
        backend = start.get("backend") or ""
        if start.get("type") != "run_start" or start.get("lab") != "02" or end.get("type") != "run_end":
            continue
        if backend.startswith("harness.scripted") or backend == "never-stops":
            continue
        if start.get("issue") not in answers or config_of(start) not in CONFIGS:
            continue
        if any(r["type"] == "end" and r["terminal_reason"] in UNFINISHED for r in records):
            continue   # stopped by quota / rate limit, not finished
        runs[config_of(start)].append(records)
    return runs


def right(body: str | None, answer: dict) -> bool:
    """The same rule as check(), on a draft's text instead of a posted comment."""
    label, file = parse_triage_comment(body or "")
    return label == answer["label"] and file == normalize_path(answer["file"])


def median(xs: list[float]) -> float | None:
    """Nearest-rank median."""
    return sorted(xs)[max(0, math.ceil(len(xs) / 2) - 1)] if xs else None


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def summarize(config: str, runs: list[list[dict]], answers: dict, tracker: Tracker) -> dict:
    planned, second = config != "agent", config in ("planned_self", "planned_external")
    per_issue, per_sweep = defaultdict(int), defaultdict(int)
    tokens, latency, plan_share, crit_share, revisions, rounds = [], [], [], [], [], []
    plan_alone = defaultdict(list)    # number of model calls -> share of the plan call alone
    crit_time = []                    # seconds spent in critique calls, per run
    changed = fixed = broken = held = repeats = off_plan = 0
    for records in runs:
        start = records[0]
        issue, run_id = start["issue"], start["run_id"]
        ok = check(tracker, run_id, issue, answers[issue])
        per_issue[issue] += ok
        per_sweep[start["sweep"]] += ok
        calls = [r for r in records if r["type"] == "model_call"]
        total = sum(c["usage"]["prompt_tokens"] for c in calls)

        def share(purposes):
            part = sum(c["usage"]["prompt_tokens"] for c in calls if c.get("purpose") in purposes)
            return part / total if total else 0.0

        tokens.append(sum(c["usage"]["prompt_tokens"] + c["usage"]["output_tokens"] for c in calls))
        latency.append(records[-1]["elapsed_s"])
        plan_share.append(share(("plan", "revise")))
        crit_share.append(share(("critique",)))
        first_plan = next((c for c in calls if c.get("purpose") == "plan"), None)
        if first_plan and total:
            plan_alone[len(calls)].append(first_plan["usage"]["prompt_tokens"] / total)
        crit_time.append(sum(c.get("latency_s") or 0 for c in calls if c.get("purpose") == "critique"))
        repeats += bool(repeat_check(records))
        off_plan += sum(1 for r in records if r["type"] == "off_plan")
        revisions.append(sum(1 for r in records if r["type"] == "revision"))
        lines = [r for r in records if r["type"] == "critique"]
        rounds.append(len(lines))
        if any(x["changed"] for x in lines):
            changed += 1
            before = right(lines[0]["draft"], answers[issue])
            fixed += (not before) and ok
            broken += before and not ok
        if lines and lines[-1]["result"] == "fail" and not tracker.comments(run_id=run_id):
            held += 1
    sweeps = sorted({r[0]["sweep"] for r in runs})
    return {
        "runs": len(runs),
        "passes": sum(per_issue.values()),
        "per_issue": {str(i): per_issue[i] for i in sorted(answers)},
        "sweep_min": min((per_sweep[s] for s in sweeps), default=None),
        "sweep_max": max((per_sweep[s] for s in sweeps), default=None),
        "mean_tokens": mean(tokens),
        "p50_latency_s": median(latency),
        "max_latency_s": max(latency, default=None),
        "plan_overhead_share": mean(plan_share) if planned else None,
        "critique_overhead_share": mean(crit_share) if second else None,
        "runs_with_repeat": repeats,
        "off_plan": off_plan if planned else None,
        "revisions_mean": mean(revisions) if planned else None,
        "revisions_max": max(revisions, default=None) if planned else None,
        "rounds_mean": mean(rounds) if second else None,
        "rounds_max": max(rounds, default=None) if second else None,
        "drafts_changed": changed if second else None,
        "drafts_fixed": fixed if second else None,
        "drafts_broken": broken if second else None,
        "drafts_held": held if second else None,
        # not in the json, only for the table
        "_plan_alone": {n: mean(v) for n, v in sorted(plan_alone.items())} if planned else None,
        "_critique_s": mean(crit_time) if second else None,
    }


def table(results: dict) -> str:
    def cell(r, f):
        try:
            return f(r)
        except TypeError:   # a None field: the measure does not apply
            return ""

    pct = lambda x: f"{100 * x:.0f}%"  # noqa: E731
    plain = lambda x: "" if x is None else str(x)  # noqa: E731
    rows = [
        ("Passes, of 18", lambda r: f"{r['passes']} (of {r['runs']} runs)"),
        ("Per issue, 4201 to 4206, of 3 each", lambda r: " ".join(str(v) for v in r["per_issue"].values())),
        ("Spread: the lowest and highest sweep, of 6", lambda r: f"{r['sweep_min']} to {r['sweep_max']}"),
        ("Tokens per run", lambda r: f"{r['mean_tokens']:,.0f}"),
        ("Latency per run: median, and slowest", lambda r: f"{r['p50_latency_s']:.1f} s, {r['max_latency_s']:.1f} s"),
        ("Plan overhead: the mean share of prompt tokens, and how it changes with run length",
         lambda r: pct(r["plan_overhead_share"]) + "; plan call alone by model calls: "
         + ", ".join(f"{n}: {pct(s)}" for n, s in r["_plan_alone"].items())),
        ("Second-pass overhead: the mean share of prompt tokens",
         lambda r: f"{pct(r['critique_overhead_share'])} ({r['_critique_s']:.1f} s a run)"),
        ("Runs with at least one exact repeat", lambda r: str(r["runs_with_repeat"])),
        ("`off_plan` lines", lambda r: plain(r["off_plan"])),
        ("Revisions per run: mean, and maximum", lambda r: f"{r['revisions_mean']:.2f}, {r['revisions_max']}"),
        ("Second-pass rounds per run: mean, and maximum", lambda r: f"{r['rounds_mean']:.2f}, {r['rounds_max']}"),
        ("Drafts changed; of those fixed, and broken",
         lambda r: "" if r["drafts_changed"] is None
         else f"{r['drafts_changed']}; {r['drafts_fixed']} fixed, {r['drafts_broken']} broken"),
        ("Drafts held at the bound", lambda r: plain(r["drafts_held"])),
    ]
    out = ["| Measure | Agent | Planned, off | Planned, self | Planned, external |", "|---|---|---|---|---|"]
    for name, f in rows:
        out.append(f"| {name} | " + " | ".join(cell(results[c], f) for c in CONFIGS) + " |")
    return "\n".join(out)


NOTES_ISSUES = (4203, 4204, 4206)


def context_entries() -> list[dict]:
    """Part G: one entry per finished notes run on 4203, 4204, 4206 (FORMATS 10.10)."""
    out = []
    for path in sorted((ROOT / "runs").glob("notes-*.jsonl")):
        records = read_trace(path)
        start, end = records[0], records[-1]
        backend = start.get("backend") or ""
        if start.get("lab") != "02" or start.get("issue") not in NOTES_ISSUES or end.get("type") != "run_end" \
                or backend.startswith("harness.scripted") or backend == "never-stops":
            continue
        if any(r["type"] == "end" and r["terminal_reason"] in UNFINISHED for r in records):
            continue
        seen, compacted, asked, questions = [], False, False, []
        for r in records:
            if r["type"] == "tool_call":
                seen += [x for x in r.get("paths") or [] if x not in seen]
            elif r["type"] == "compaction" and not asked:
                compacted = True
            elif r["type"] == "question":
                asked = True
                questions.append({"path": r["path"], "truth": "yes" if r["path"] in seen else "no",
                                  "answer": r["answer"]})
        out.append({"run_id": start["run_id"], "issue": start["issue"], "notes_shown": start.get("notes") == "on",
                    "compacted": compacted, "questions": questions,
                    "right": sum(q["truth"] == q["answer"] for q in questions)})
    return out


def main() -> None:
    answers = {int(k): v for k, v in yaml.safe_load((ROOT / "fixtures/answers.yaml").read_text())["eval"].items()}
    tracker = Tracker(ROOT)
    runs = load_runs(answers)
    results = {c: summarize(c, runs.get(c, []), answers, tracker) for c in CONFIGS}

    report_path = ROOT / "reports/LAB_02.json"
    report = json.loads(report_path.read_text())
    report["results"] = {c: {k: v for k, v in r.items() if not k.startswith("_")} for c, r in results.items()}
    report["context"] = context_entries()
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(table(results))
    print("\n| Run | Issue | Progress file shown | Compacted before the questions | Answers right, of 6 |")
    print("|---|---|---|---|---|")
    for e in sorted(report["context"], key=lambda e: (e["notes_shown"], e["issue"], e["run_id"])):
        print(f"| `{e['run_id']}` | {e['issue']} | {'yes' if e['notes_shown'] else 'no'} | "
              f"{'yes' if e['compacted'] else 'no'} | {e['right']} |")


if __name__ == "__main__":
    main()
