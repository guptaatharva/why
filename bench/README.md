# Upgrade benchmark

`uv run python bench/run.py` runs `why upgrade` on 8 small cases in `cases.py`:

- 6 behavioural breaks: the library keeps its signature but changes behaviour (timezone dropped,
  rounding mode, `None` instead of `[]`, sort order, a retry default, case folding).
- 2 controls: upgrades that must not be blocked.

For each case it builds the old and new library wheels, creates a client repo, records the
team's decision, approves it (guard generated and run 3×), and checks the upgrade. It reports
breaks caught, whether the root-cause API was named correctly, and false positives.

Current result: **6/6 caught, 6/6 root causes correct, 0/2 false positives.** These cases were
written alongside Why and only show that the pipeline works. They are not an independent
measurement.

## BBCBench

The PRD's hackathon metric is breaks caught on a subset of BBCBench, the 100 Python and
JavaScript behavioural dependency breaks from "Who Broke Me?"
([arXiv 2610.09267](https://arxiv.org/abs/2610.09267)). The dataset was not publicly linked
when this was built. To run it once it is available, each case needs:

1. the client project at the breaking commit,
2. the library's old and new versions, and
3. the failing test.

Record that failing test as the decision's guard (drop it into `why_guards/test_dNNNN_*.py` and
`why approve`), then run `why upgrade <lib>@<new> --from <old>`. A case counts as caught when the
verdict is `blocked`. Score the root cause against the paper's ground-truth API.

# Scale check

`uv run python bench/scale.py` checks the PRD requirement that guard checks finish in under 5
minutes on a 50k-line repo. It generates a 50k-line Python service with 200 commits and 24
decisions (6 call-count, 12 return-value and 6 forbidden-pattern guards over the whole tree), then
times mining, approval and `why check`.

Latest run (laptop, offline):

| Step | Time |
|---|---|
| `why mine` (rules, 200 commits, 504 files) | 0.5s |
| `why approve` ×24 (3 stability runs each) | 19.6s |
| `why check`, cold | 0.4s |
| `why check`, same tree (cache) | 0.03s |
| `why check`, after a one-line edit | 0.4s |

This measures Why's own overhead: copying the sandbox, indexing, and running the guards. The code is
synthetic and has no dependencies. On a real repo, the time to install dependencies into the
sandbox (`install:`) and to import the project usually dominates.

`--lines 200000` runs a bigger repo, and `--json out.json` saves the timings.
