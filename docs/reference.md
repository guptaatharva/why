# Why reference

Configuration, environment, Teams mode, CI and the GitHub App. For the overview and quickstart, see the
[README](../README.md).

## Repository layout

```
.why/config.yaml            settings (below)
.why/decisions/D-0001.yaml  one file per decision: rule, reason, source, guard, approvals
.why/log.jsonl              every state change: who, when, why          (versioned)
.why/evidence.jsonl         every guard run                             (gitignored)
.why/consults.jsonl         what agents asked over MCP                  (gitignored)
.why/reports/               check / upgrade / merge / assumption runs   (gitignored)
.why/cache/                 guard results keyed by code tree + guard    (gitignored)
why_guards/                 the generated tests
```

## `.why/config.yaml`

```yaml
language: python            # python | javascript
guards_dir: why_guards
python_path: ["."]          # import roots, e.g. ["src"]
install: []                 # pip args installed into each sandbox, e.g. ["-r", "requirements.txt"]
setup: []                   # shell commands run in the sandbox first
test_runner: vitest         # javascript only: vitest | jest (default: whichever package.json uses)
package_manager: pnpm       # javascript only: pnpm | yarn | npm (default: from the lockfile)
sandbox: local              # local | nebius
timeout_s: 300
cache: true                 # reuse a guard's result for an unchanged code tree + guard
find_links: /path/to/wheels # extra wheel dir / index for `why upgrade` (private packages)
registry: https://why.example.com   # where `why upgrade` fetches published guarantees
mining:
  days: 90
  max_per_source: 3         # cap on proposals per commit / PR / session
  min_confidence: 0.45
  session_dirs: [".why/sessions"]          # plain transcript folders
  claude_project_dirs: []                  # extra folders Claude Code sessions were started in
nebius:
  platform: cpu-d3
  preset: 4vcpu-16gb
  image: python:3.12-slim   # Python guards
  image_js: node:22-slim    # JS/TS guards
  bucket: why-sandboxes     # object storage for sandboxes over 64 KiB
  endpoint: https://storage.eu-north1.nebius.cloud
  region: eu-north1
team: {}                    # see Teams below
```

## Environment

| Variable | Purpose |
|---|---|
| `NEBIUS_API_KEY` | Nemotron on Nebius Token Factory. Without it Why uses its rule-based miner and guard templates. |
| `WHY_LLM_BASE_URL`, `WHY_LLM_API_KEY` | Any other OpenAI-compatible endpoint |
| `WHY_MODEL_NANO` / `_SUPER` / `_ULTRA` | Exact model ids; otherwise resolved from the provider's `/models` list (`why models --all`) |
| `TAVILY_API_KEY` | `why assume` |
| `GITHUB_TOKEN` | Mining PR bodies and comments (falls back to `gh auth token`) |
| `WHY_GITHUB_APP_ID`, `WHY_GITHUB_PRIVATE_KEY`, `WHY_GITHUB_WEBHOOK_SECRET` | GitHub App |
| `NEBIUS_S3_ACCESS_KEY_ID`, `NEBIUS_S3_SECRET_ACCESS_KEY` | Object storage for the Nebius sandbox. Jobs only get a 1-hour presigned GET URL. |
| `WHY_DASHBOARD_WRITE=0` | Read-only dashboard |
| `WHY_DASHBOARD_TOKENS` | `name:token,…`: dashboard changes need a member token (Teams mode) |
| `WHY_MCP_TOKEN` | Bearer token for MCP over HTTP; without it `/mcp` is read-only |
| `WHY_REGISTRY_TOKEN`, `WHY_REGISTRY_URL` | Publishing to / reading from a guarantee registry |
| `WHY_ACTOR` | Who you are in the decision log (default: git user.name) |
| `WHY_OFFLINE=1`, `WHY_NO_CACHE=1` | Never call a model; never reuse cached guard results |

`.env.example` has all of these. Why reads the environment; it does not load `.env` itself.

## Teams

Add a `team:` block and Why enforces roles, code owners and multi-person approval in the CLI, the dashboard
and the GitHub App:

```yaml
team:
  members:
    maya: {role: admin, aliases: [maya@larkspur.example]}   # matched against WHY_ACTOR, git user.name / user.email
    theo: {role: reviewer}
    rafa: {role: reviewer}
    ines: {role: viewer}
  owners:                       # CODEOWNERS-style path globs
    "larkspur/payments/**": [rafa]
  required_approvals: 2
```

- **viewer** (and anyone not listed): read only.
- **reviewer**: approve, reject and edit proposed decisions.
- **admin**: also retire and supersede. Owners may retire or supersede decisions on their own paths.
- A decision activates after `required_approvals` different people approve it. If it touches an owned
  path, one approval must come from an owner.
- `why review --mine` lists what is waiting on you. `why team` shows roles and who you are.
- In the dashboard, set `WHY_DASHBOARD_TOKENS` and members sign in on the Review tab.

## Metrics

`why stats` (and the dashboard's Success metrics card) computes the PRD's metrics from the log and evidence:

- **Approval rate**: mined decisions approved at review, out of those reviewed. Target ≥ 60%.
- **Breaks caught**: guard failures on PRs, merges and upgrades.
- **Overridden after a violation**: decisions superseded or retired after their guard fired, instead of
  the code being fixed. This is an upper bound on false positives. Target < 10%.
- **Prevention**: agents that asked over MCP before editing, and self-checks that caught a break.

`GET /api/stats` on the server adds the cross-repo view: repos installed, and repos with a violation caught
per week.

## CI without the GitHub App

```yaml
# .github/workflows/why.yml
on: pull_request
jobs:
  why:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: guptaatharva/why@main
```

The action runs `why check` with guards from the base branch. If the PR bumps a dependency (lockfile,
`requirements*.txt`, `pyproject.toml`, `package.json`), it runs `why upgrade --base` from a clean worktree
of the base branch.

## GitHub App

1. Create a GitHub App with these permissions:
   - Checks: write
   - Contents: write
   - Pull requests: write
   - Metadata: read

   Subscribe to `pull_request`, `push`, `installation` and `installation_repositories`. Set the webhook
   URL to `https://<host>/github/webhook`.
2. Run the server (`why serve`, or the `Dockerfile`) with `WHY_GITHUB_APP_ID`, `WHY_GITHUB_PRIVATE_KEY` (a path
   to the `.pem`, or its contents) and `WHY_GITHUB_WEBHOOK_SECRET` set.
3. Install the app on a repo that has a committed `.why/`. Each PR gets a **Why** check run:
   - It names every violated decision with its reason and source.
   - If the PR bumps dependencies, it runs an upgrade check for each bump and names the root cause.
   - It merges the PR with up to 5 other open PRs on the same base. A decision that breaks only when
     they are combined is reported as a neutral check.

## Nebius sandbox

With `sandbox: nebius`:
- Each check is packaged as a tarball and run as a Nebius Serverless AI job. `nebius` CLI must be logged in.
- Python guards run in `nebius.image`. JS/TS guards install dependencies from the repo's lockfile and run
  in `nebius.image_js`.
- Bundles up to 64 KiB go in with `--inject-file`. Bigger ones are uploaded to `nebius.bucket`, and the job
  downloads them with a presigned URL. The bundle is deleted when the job finishes.
- `why doctor` checks the CLI and the bucket.

## Benchmarks

- `bench/run.py`: upgrade root-cause cases.
- `bench/scale.py`: guard-check time on a generated 50k-line repo.

See [bench/README.md](../bench/README.md).
