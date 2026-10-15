# ARCHITECTURE.md · RepoMind on the seven layers

For each layer: what does this job, and where is it enforced. Cite code as a file
path and line number joined by a colon, inside backticks; a checker opens every
reference (`checks/FORMATS.md`, section 8). A layer held only by a sentence in a
prompt (any file under `prompts/`) is **EMPTY**, and so is a layer nothing does
yet. Status is one of: present, partial (say what is missing), or **EMPTY**. Each
assignment brings this file up to date.

What each layer is for (Lecture 1, slides 40 to 42):

- **Model**: reasoning, choosing tools, generating text.
- **Harness**: runs the loop, builds each prompt, trims old history.
- **Tools**: where the system acts on the world.
- **Memory & State**: what survives a step, a session, a restart.
- **Control & Policy**: what the system may do, and when it must ask.
- **Observability**: a step-by-step record of what it did.
- **Evaluation**: measuring quality, before and after release.

The table below is RepoMind as Assignment 2 left it. The Harness row now cites the
validator and the boundary; Memory & State, Observability and Evaluation changed
with the plan, the progress file, the detectors and the second pass.

| Layer | What does this job | Where it is enforced | Status |
|---|---|---|---|
| Model | The configured model, called only through the metered client | `harness/client.py:116` | present |
| Harness | The loop, with three bounds checked in code before every call; in the planned way, the validator runs before any tool call (schema check, then the four checks, then reject and re-plan or escalate), and the boundary refuses any call that is not the current step's tool | `agent/loop.py:38`, `agent/loop.py:41`, `agent/loop.py:44`, `agent/validator.py:39`, `ways/planned.py:117`, `ways/planned.py:370`, `ways/planned.py:272`, `ways/planned.py:252` | present |
| Tools | Four tools behind one registry (read_file arrived with Assignment 2's starter); post_comment stamps the run_id | `tools/__init__.py:52`, `harness/tracker.py:60` | present |
| Memory & State | The notes way writes a progress file (steps done, paths seen) after every step, outside the conversation | `ways/notes.py:83` | partial: only in the notes way, and only for one run |
| Control & Policy | Nothing decides which writes need a person; the prompt asks politely | none | **EMPTY** |
| Observability | One trace line per model call and per tool call; the plan and each revision are logged as versions, with off_plan, revision, critique and compaction events | `agent/loop.py:50`, `ways/planned.py:377`, `ways/planned.py:232`, `ways/planned.py:273` | partial: traces, no alerts |
| Evaluation | The grounded check reads the tracker, never the agent; detectors and a diff read the plan against the trace; the external second pass checks the draft in code | `eval/check_triage.py:7`, `agent/detectors.py:42`, `agent/diff.py:78`, `agent/critique.py:55` | partial: offline only |

**Empty layers: 2 of 7**, and two more are partial. Lecture 1, slide 46 expects
four of the seven to be missing from a first build; Assignment 1 filled
Observability and Evaluation just enough to measure.

**After Assignment 2: 1 of 7 empty**, and three are partial. The plan made the
Harness stronger (a validator before any tool, an executor that cannot leave the
plan), and the progress file is a first bit of Memory & State. Control & Policy is
still empty: nothing asks a person yet, but the logged plan is the one place an
approval could go (Lecture 5).
