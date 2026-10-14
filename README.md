# Why

Why turns the decisions behind agent-written code into executable tests, and runs them on every change.

Coding agents make a decision in one session ("never retry `charge()`: the gateway double-bills") and a
later session quietly undoes it to fix something else. The project's tests still pass, because nobody
wrote the decision down as a test. Nobody notices until it breaks in production.

## How it works

```
 git history, PR threads,          decision                       guard                          checked on
 agent session logs        ──▶  "never retry charge()   ──▶  pytest / Vitest / Jest  ──▶  every PR, merge of parallel
 (Claude Code, Codex,            because it double-bills"     test that fails if the        agent branches, and
 Entire Checkpoints)             + source quote + link        rule is broken                dependency upgrade
        why mine                   why approve                  (run 3x before activation)    why check / merge / upgrade
```

1. **Mine.** `why mine` reads commits, PR bodies and comments, and local agent session logs. It proposes
   decisions, each with the quote and link it came from. Nothing activates without a human approval.
2. **Guard.** `why approve` compiles a decision into a test in the repo's own framework. It uses
   templates (call count, return-value predicate, forbidden pattern), or a model for custom rules. The
   test must pass 3 times in a row before the decision goes active; flaky guards are quarantined.
3. **Check.** `why check` runs every active guard in a throwaway sandbox. Guards and decisions always
   come from the trusted base branch, so a change cannot weaken the test that judges it.
   `why upgrade pkg@ver` runs the guards before and after a dependency bump and names the library
   function whose behaviour changed. `why merge a b` merges parallel agent branches in a sandbox and
   finds decisions one branch breaks for the other.
4. **Prevent.** An MCP server lets coding agents ask which decisions govern a file before editing it,
   and run the guards against their diff before opening a PR.

Decisions live in `.why/decisions/*.yaml` and guards in `why_guards/`. Both are versioned in git with the code.

## Status

**Works today** (covered by 53 offline tests and the demo script):

- Mining from git history and local agent session logs, using the rule-based miner
- Guard templates for Python (pytest) and TypeScript/JavaScript (Vitest, Jest), with 3x stability runs
- `why check` in a sandbox with trusted guards; results cached per code tree
- `why upgrade` with root-cause diffing for PyPI and npm packages. It caught 6/6 breaks and had 0/2
  false positives on 8 hand-written benchmark cases in `bench/`. That is a small self-made set, not
  BBCBench.
- `why merge` across parallel branches
- `why assume`: checks outside facts (vendor limits) against search results. Tested with recorded
  responses.
- MCP server (`why mcp` over stdio; `/mcp` over HTTP)
- Web dashboard (`why serve`)
- `why blame`, `why log`, `why audit` (JSON/CSV/HTML)
- Teams mode: roles, code owners, required approvals, review queue
- Published guarantees: `why publish` plus a registry that `why upgrade` pulls from. Tested with an
  in-process server.

**In progress** (code exists, not yet verified against the real service):

- Nemotron models on Nebius Token Factory for mining, guard writing and explanations (tested only with
  a stubbed model)
- Live Tavily search
- GitHub App check runs (tested against a mocked GitHub API)
- Nebius Serverless Jobs as the sandbox backend, including object storage for repos over 64 KiB
- Entire Checkpoints input (tested on a synthetic checkpoint branch, not real Entire output)
- `Dockerfile` for the hosted demo (not built yet)
- Decisions of kind `custom` (e.g. "prices in integer cents") need a model to write the guard. Offline,
  they stay proposed.

**Next**

- Run the full flow with live Nemotron and Tavily keys
- Deploy the hosted demo and register the GitHub App
- Evaluate `why upgrade` on a BBCBench subset

## Quickstart

Requires Python 3.11+, git and [uv](https://docs.astral.sh/uv/). Node 22 is needed only for JS/TS repos
and for rebuilding the dashboard.

```bash
uv tool install git+https://github.com/guptaatharva/why
cd your-repo
why init
why mine                 # propose decisions from history
why review               # see what was proposed
why approve D-0001       # generate the guard, run it 3x, activate it
why check                # exit 1 if any decision is violated
```

From source:

```bash
git clone https://github.com/guptaatharva/why && cd why
uv sync
uv run pytest -q                              # 53 tests, offline
FAST=1 bash demo/run_demo.sh --no-serve       # the demo story below, about 30s
bash demo/run_demo.sh                         # same, then the dashboard on http://127.0.0.1:7357
```

No API keys are needed for any of the above. `.env.example` lists the optional keys (Nebius Token
Factory, Tavily, GitHub).

## Demo

`demo/run_demo.sh` builds Larkspur, a fictional storefront repo with two months of agent-written history,
and runs the whole story offline. Decision D-0002 is mined from this commit message:

> Never retry `charge()` on a timeout: the gateway double-bills on retry, because the first request may already have captured the payment.

An agent later adds a 3x retry around `charge()` to fix checkout timeouts, and the project's own tests stay
green. Why's check on that branch (code from the branch, guards from `main`):

```
$ why check --ref agent/fix-checkout-timeouts --base main
Why check · 4 guard(s) · b3dcc12d56 · 1.71s

  PASS  D-0001 `parse_date('2026-03-01T10:00:00+05:30')` must always return UTC
  FAIL  D-0002 Never retry `charge()` on a timeout
        reason: the gateway double-bills on retry, because the first request may already have captured the payment
        source: 5699fbb44cb01654f04e7d0d4bddf0ac3194451d
        AssertionError: larkspur.payments.gateway:charge was called 3 times after a TimeoutError (allowed: 1). Violates D-0002 ...
  PASS  D-0003 Don't call `print()` in larkspur/
  PASS  D-0005 Send at most 100 SMS per minute

1 decision(s) violated. Fix the code, or `why supersede <id> --reason ...`.
```

The same run blocks a dependency bump (`chronokit` 1.4.0 to 2.0.0). It names the root cause:
`chronokit.parse` stops returning UTC, which breaks D-0001.

```
  BREAK D-0001 `parse_date('2026-03-01T10:00:00+05:30')` must always return UTC
        root cause: chronokit.parse
        before: datetime.datetime(2026, 3, 1, 4, 30, tzinfo=datetime.timezone.utc)
        after:  datetime.datetime(2026, 3, 1, 10, 0)
```

A smaller example you can run in any git repo:

```bash
why init
why add "Never call print() in app code" --kind forbid_pattern \
  --params '{"pattern": "print\\(", "paths": ["app.py"]}' --reason "logs go through the logger"
why approve D-0001
echo 'print("hi")' >> app.py
why check     # FAIL D-0001 ... Reason: logs go through the logger  (exit code 1)
```

All company, vendor and package names in the demo are fictional.

## Commands

| Command | What it does |
|---|---|
| `why init`, `why mine`, `why review` | Set up `.why/`, propose decisions from history, list what awaits review |
| `why approve / reject / supersede / retire <id>` | Change a decision's state; every change is logged with who and why |
| `why check [--ref X --base main]` | Run active guards in a sandbox |
| `why upgrade <pkg>@<ver>` | Run guards before and after a dependency bump; report the root cause |
| `why merge <branch>...` | Run every branch's guards on the merged result |
| `why assume` | Re-check outside facts a decision depends on |
| `why log`, `why blame file:line`, `why audit` | History, which decisions govern a line, export for review |
| `why mcp`, `why mcp-install` | Serve decisions to coding agents over MCP |
| `why publish`, `why guarantees` | Publish decisions as guarantees of a library release; list a package's guarantees |
| `why serve` | Dashboard, JSON API, GitHub App webhook, MCP over HTTP, guarantee registry |
| `why doctor` | Check keys, models, sandbox and MCP setup |

## Tech stack

- Python 3.11+: argparse CLI, FastAPI, pytest, and the MCP Python SDK
- React + TypeScript + Vite dashboard
- NVIDIA Nemotron Nano / Super / Ultra on Nebius Token Factory (OpenAI-compatible API)
- Tavily search
- Nebius Serverless Jobs and Object Storage
- GitHub Apps API

## Built for

Nebius x NVIDIA Global AI Hackathon (submissions close Oct 30, 2026).

## Author

Atharva Gupta, [github.com/guptaatharva](https://github.com/guptaatharva)
