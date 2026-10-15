# Assignment 2 report · A plan RepoMind cannot quietly abandon

Every number here that has a field in `reports/LAB_02.json` goes there too: the
checker reads that file (format: `checks/FORMATS.md`, section 10.10). Give each
number its run count. Mark any vendor figure you cite with its source and the date
you read it.

## Part A · The plan artifact
I chose `args_json`, so each step's arguments are a JSON string that the executor
parses and checks against the tool's schema before the call. It costs no union
types and the schema does not change when a tool is added, while `oneOf` would
spend the union budget on every tool's shape.

My schema has 0 optional fields (of 24) and 2 union types (of 16): `tool` and
`expected_result`, both `["string", "null"]`. A strict request carrying it was
accepted by Gemini (`gemini-3.5-flash-lite`, 2026-10-12), but Gemini does not
promise to enforce it, so every plan is still checked against the schema.

## Part B · The validator and the boundary

| Plan | Valid | Failures (step, check) |
|---|---|---|
| `good_export_fix.json` | yes | none |
| `good_triage.json` | yes | none |
| `s18_specimen.json` | no | (2, 2), (3, 3), (5, 1) |

`MAX_REPLANS = 2`: a bad plan is usually a small format mistake that one retry with
the error list fixes, and if it fails 3 times the planner is stuck, so more tries
only waste tokens.

Plans that broke my schema or did not end with `post_comment`: 5 of 67 (62 planned
runs on a real model, dev and eval, counted with `python -m eval.count_schema`).

## Part C · Revision at a checkpoint
After the first search (and after any search a revision adds or changes), my code
checks that the search returned at least one source file, a `.ts` or `.tsx` under
`src/` that is not in `src/i18n/`, and the plan is only revised when that check fails.

`MAX_REVISIONS = 2`: one new search, and one more try if that one also finds no
source file or the revision is rejected (Gemini sometimes leaves `tool` out of
revised steps); after that the reviser is just guessing, so the run goes on with
the plan it has.

## Part D · A second pass before posting
My four checks in `verify`:
1. **Parses:** `parse_triage_comment` finds a known label and a `file:` line (a path or `none`).
2. **Exists:** the path is a real file under `fixtures/repo`.
3. **Seen:** the path is in the `paths` of a `tool_call` line in this run's trace (a search hit or a read).
4. **Agree:** `question` goes with `none`, and every other label names a file.

`MAX_ROUNDS = 2`: check, rewrite once with the reason, check again. If one rewrite
doesn't fix it, a second one probably won't either, and each round costs a call.

| Mode | A check can find the error | The critic knows more than the agent | A limit and a log |
|---|---|---|---|
| `off` | no | no | no (there is nothing to limit) |
| `self` | no, it's the same model's opinion | no, same issue and same results | yes |
| `external` | yes, for the 4 checks (not for a wrong file the run did see) | yes, the repo and the run's own trace | yes |

## Part E · Detectors and the diff
**How the diff matches a call to a step.** If the call has `plan_step` (my planned
way writes it), that decides, and a retry of the same step still belongs to it.
Otherwise the call goes to the first step in plan order that has no call yet, has
the same tool, and has every argument the plan fixed (not `"$fill"`) equal to the
call's. A call that fits no step is `extra`. So slide 51's second `run_tests` (with
`test: test_export_csv`) can only be step 5, and the second `"export"` search is extra.

**The looping run.**
1. Did two steps do the same thing? **Yes**: turns 1 and 3 (`search("export")`) and 2 and 4 (`read(ReportsPage.tsx)`).
2. Is a step's result never used? **Yes**, turns 1 and 2. The plan has no `depends_on`
   and the results have no paths, so I judged it from the trace: if the agent makes
   the exact same call again later, it didn't use what it got the first time.
3. Did every step run? **No**: steps 3 to 6 (identify, fix, verify, PR) never ran;
   the turn limit hit after the agent wasted 2 of its 7 turns on repeats.
4. The plan roughly fits the task, but step 1 (`"export"`, 247 results) is too broad to
   lead anywhere, and turn 5's `exportButton` is the search the plan should have had.

**Slide 51's run.**
1. Did two steps do the same thing? **Yes**: steps 2 and 4, the same `search_repo("export")`.
2. Is a step's result never used? **No**: every step is in the next one's `depends_on`.
3. Did every step run? **No**: step 6, "confirm the button works", never ran, and the
   agent still reported "fixed" (its step 5 test was even still failing).
4. The plan fits the task, since it ends by checking the button itself; the run just
   stopped before that step and claimed success.

Diff labels: steps 1 to 5 `executed`, step 6 `skipped`, and trace step 4 (the second
search) is `extra`.

## Part F · What the plan and the second pass bought
3 sweeps per configuration, so a pilot only. 72 runs on `gemini-3.5-flash-lite`,
after `make reset`.

| Measure | Agent | Planned, off | Planned, self | Planned, external |
|---|---|---|---|---|
| Passes, of 18 | 13 (of 18 runs) | 10 (of 18 runs) | 11 (of 18 runs) | 11 (of 18 runs) |
| Per issue, 4201 to 4206, of 3 each | 3 1 3 3 3 0 | 3 2 3 0 2 0 | 3 3 3 1 1 0 | 3 2 3 0 3 0 |
| Spread: the lowest and highest sweep, of 6 | 4 to 5 | 2 to 4 | 3 to 4 | 3 to 4 |
| Tokens per run | 12,155 | 7,580 | 9,183 | 7,577 |
| Latency per run: median, and slowest | 4.7 s, 9.3 s | 5.4 s, 8.5 s | 6.1 s, 10.8 s | 5.3 s, 9.8 s |
| Plan overhead: the mean share of prompt tokens, and how it changes with run length |  | 34%; plan call alone by model calls: 4: 21%, 6: 16%, 7: 11%, 8: 11% | 24%; plan call alone by model calls: 5: 17%, 7: 11%, 8: 10%, 9: 9%, 10: 7% | 32%; plan call alone by model calls: 4: 22%, 5: 16%, 6: 15%, 7: 12%, 8: 12% |
| Second-pass overhead: the mean share of prompt tokens |  |  | 21% (0.7 s a run) | 5% (0.3 s a run) |
| Runs with at least one exact repeat | 1 | 0 | 0 | 0 |
| `off_plan` lines |  | 2 | 0 | 1 |
| Revisions per run: mean, and maximum |  | 1.06, 2 | 0.89, 2 | 0.83, 2 |
| Second-pass rounds per run: mean, and maximum |  |  | 1.11, 2 | 1.22, 2 |
| Drafts changed; of those fixed, and broken |  |  | 2; 1 fixed, 0 broken | 5; 2 fixed, 0 broken |
| Drafts held at the bound |  |  | 0 | 0 |

Made by `python -m eval.summarize_02` (also writes `results` in `LAB_02.json`).

1. **What did the plan buy RepoMind, concretely?**
   - Every plan was logged before the first tool call, and bad plans were stopped
     before running (4 of 58 plans, 22 of 50 revisions rejected, mostly Gemini leaving
     out `tool`).
   - 3 off-plan calls were refused, and 3 runs on 4204 escalated instead of guessing.
   - A diff and ladder for every run, since each call has its `plan_step`.
   - Fewer tokens (7.6k vs 12.2k a run) and no repeats. On 4206 the agent hit the turn
     limit 3 times; the plan stopped after one read.
   - It did not buy passes: 10 to 11 of 18 vs the agent's 13 (lost mostly on 4204).

2. **Where did it not pay?** On 4201 and 4203 the steps are known in advance (the
   agent did the same calls every run), so a fixed workflow is enough. On 4201 the plan
   cost more than the work (4.6k vs 4.3k tokens), and on 4205 (a question, no file) the
   plan and revision took 3.2k tokens for nothing.

3. **What did each second pass buy, and what did it cost?**
   - **off:** caught nothing (4205 posted `question` with a file and failed).
   - **self:** +21% prompt tokens, +0.7 s a run. Changed 2 drafts, fixed 1. It still
     accepted `bug / none` and `question` with a file, which a code check catches.
   - **external:** +5% tokens, +0.3 s. Changed 5 drafts, fixed 2 (both 4205). On 4206
     it caught a file the run never saw, but the rewrite was still wrong.
   - None broke a passing draft and none was held. 4206 was 0 of 3 in every mode: the
     run did see `csv.ts`, so it is a wrong-meaning error no check can catch.
   - Gates: off passes none; self only the limit and log; external passes all three,
     but only for format and grounding errors.

4. **Which failure did no detector catch?** All 9 planned runs on 4206 ended
   `completed` with every step done, so `expected_result_check` and the external
   checks found nothing, yet 0 of 9 were right. Only `check()` against the answer key
   showed it.

## Part G · Note-taking against compaction
`COMPACT_AT = 400` tokens so every run compacts mid-run (a test setting). The progress
file has `about`, `steps_done` and `paths` (every path seen), never search terms.

| Run | Issue | Progress file shown | Compacted before the questions | Answers right, of 6 |
|---|---|---|---|---|
| `notes-4203-s01-0d9b15` | 4203 | no | yes | 3 |
| `notes-4203-s02-ff2d85` | 4203 | no | yes | 3 |
| `notes-4204-s01-6b6ccc` | 4204 | no | yes | 4 |
| `notes-4204-s02-20e5a9` | 4204 | no | yes | 3 |
| `notes-4206-s01-67f5c9` | 4206 | no | yes | 6 |
| `notes-4206-s02-c7557f` | 4206 | no | yes | 3 |
| `notes-4203-s01-b9592c` | 4203 | yes | yes | 6 |
| `notes-4203-s02-47ef7b` | 4203 | yes | yes | 6 |
| `notes-4204-s01-c9c166` | 4204 | yes | yes | 6 |
| `notes-4204-s02-add034` | 4204 | yes | yes | 6 |
| `notes-4206-s01-43fc20` | 4206 | yes | yes | 6 |
| `notes-4206-s02-07e4e7` | 4206 | yes | yes | 6 |

Without the file: 22 of 36 right. With the file: 36 of 36.

1. **What did the progress file keep that compaction lost?** The early side paths.
   All 14 wrong answers were "no" for a path the run had seen. The summary kept the
   main file but dropped the first search's side hits (`src/i18n/en.json`,
   `CHANGELOG.md`, ...), which are the paths the questions ask about. The file cost
   about +430 tokens a run (about 5%).

2. **Which context operations did RepoMind need?** None. My longest Part F run
   (`agent-4206-s03-57d78c`) had 12 calls, 23.7k tokens, and its biggest prompt was
   4.2k, far below the context window.
   - **Note-taking:** not needed; +430 tokens a run, only helps if history is cut.
   - **Sub-agents:** not needed; one short chain, and a sub-agent adds a call.
   - **Just-in-time retrieval:** already partly there (files are read only at the
     read step).
   - **Compaction:** not needed, and Part G shows it forgets seen paths.

## AI Assistance: Used to explain the problem and codebase, give direction on what to implement; refine wordings in the report
