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
