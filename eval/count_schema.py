"""Count for Part B's report: how many planner plans broke the schema rule
(a schema line right after a plan call), out of how many plans.

Only planned runs of this assignment on a real model, not the scripted ones.

    python -m eval.count_schema
    python -m eval.count_schema --scripted     # include scripted runs too (just to test it)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from harness import ROOT
from harness.backends import REAL_BACKENDS
from harness.trace import read_trace


def count(runs_dir: Path, scripted: bool = False) -> dict:
    runs = plans = broken = 0
    for path in sorted(runs_dir.glob("*.jsonl")):
        if path.name.startswith("_"):
            continue  # skip the ledger
        records = read_trace(path)
        start = next((r for r in records if r["type"] == "run_start"), {})
        if start.get("lab") != "02" or start.get("way") != "planned":
            continue
        if not scripted and start.get("backend") not in REAL_BACKENDS:
            continue
        runs += 1
        last_purpose = None
        for r in records:
            if r["type"] == "model_call":
                last_purpose = r.get("purpose")
                if last_purpose == "plan":
                    plans += 1
            # a schema line after a revise call is Part C's, so only count the planner's
            elif r["type"] == "schema" and last_purpose == "plan":
                broken += 1
    return {"runs": runs, "plans": plans, "broke_schema_or_no_post_comment": broken}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scripted", action="store_true", help="also count scripted runs")
    args = parser.parse_args()
    result = count(ROOT / "runs", scripted=args.scripted)
    print(f"{result['broke_schema_or_no_post_comment']} of {result['plans']} plans broke the schema "
          f"or did not end with post_comment ({result['runs']} planned runs)")
