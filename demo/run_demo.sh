#!/usr/bin/env bash
# The Why demo story, start to finish (≈30s, offline):
#   1. mine decisions from Larkspur's history       5. block a library upgrade that changes date handling
#   2. approve them: guards generated + proven      6. flag an outside assumption that is no longer true
#   3. an agent asks Why over MCP and avoids a retry 7. open the dashboard
#   4. block another agent's PR that adds the retry
#
#   demo/run_demo.sh            run the story, then serve the dashboard on :7357
#   demo/run_demo.sh --no-serve run the story only
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
REPO="$HERE/out/larkspur"
# Override for installed setups (e.g. Docker): WHY_BIN=why PY=python demo/run_demo.sh
read -r -a WHY <<< "${WHY_BIN:-uv run --project $ROOT why}"
read -r -a PY <<< "${PY:-uv run --project $ROOT python}"
DIST="$HERE/packages/dist"
pause() { [[ "${FAST:-}" == 1 ]] || sleep "${1:-1}"; }
step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; pause 0.5; }

if [[ ! -f "$DIST/chronokit-2.0.0-py3-none-any.whl" ]]; then
  for v in 1.4.0 2.0.0; do uv build -q --wheel --out-dir "$DIST" "$HERE/packages/chronokit-$v"; done
fi
uv pip install -q --python "$("${PY[@]}" -c 'import sys; print(sys.executable)')" --find-links "$DIST" chronokit==1.4.0

step "Building Larkspur, a fictional storefront with 2 months of agent-written history"
"${PY[@]}" "$HERE/build_demo.py" "$REPO" >/dev/null
cd "$REPO"
git log --format='  %C(yellow)%h%Creset %s %C(dim)(%an)%Creset' | cat

step "why init && why mine"
"${WHY[@]}" init >/dev/null
"${WHY[@]}" mine --no-github

step "Approve: each decision gets a guard, run 3× before it goes active"
cp "$HERE/guards/test_d0005_send_at_most_100_sms_per_minute.py" why_guards/   # hand-written by a teammate
"${WHY[@]}" approve D-0001 D-0002 D-0003 D-0005 || true
# Any Claude Code session opened in this repo gets the Why tools (why_decisions_for, why_run_guards, …)
printf '{\n  "mcpServers": {\n    "why": {"command": "why", "args": ["mcp"]}\n  }\n}\n' > .mcp.json
git add -A && git commit -qm "Why: guard 4 decisions"

step "Agent 2 asks Why over MCP before editing checkout.py (prevention)"
"${PY[@]}" "$HERE/agent_consults.py" "$REPO"

step "Agent 1 'fixes' checkout timeouts by retrying the charge. The project's own tests stay green:"
git checkout -qb agent/fix-checkout-timeouts
"${PY[@]}" "$HERE/agent_retry_patch.py" .
git commit -qam "Fix checkout timeouts by retrying the charge"
"${PY[@]}" -m pytest -q tests 2>&1 | tail -1
git checkout -q main

step "why check on the agent's PR (guards come from main, code from the PR)"
"${WHY[@]}" check --ref agent/fix-checkout-timeouts --base main --context pr --no-transitions || true

step "Renovate proposes chronokit 1.4.0 → 2.0.0 (same API). why upgrade:"
"${WHY[@]}" upgrade chronokit@2.0.0 --find-links "$DIST" || true

step "Two agents in parallel: each branch passes alone. why merge checks them together"
"${PY[@]}" "$HERE/parallel_agents.py" "$REPO"
"${WHY[@]}" check --ref agent/promo --base main --no-transitions >/dev/null && echo "  agent/promo alone: all decisions hold"
"${WHY[@]}" merge agent/refunds agent/promo || true

step "why assume: re-verify outside facts (recorded Tavily response; Fernwave is fictional)"
"${WHY[@]}" assume --fixture "$HERE/fixtures/fernwave_rate_limit.json" || true

step "why blame larkspur/checkout.py:20"
"${WHY[@]}" blame larkspur/checkout.py:20

step "why stats: approval rate, breaks caught before merge, prevention"
"${WHY[@]}" stats

step "why check on main"
"${WHY[@]}" check || true

if [[ "${1:-}" != "--no-serve" ]]; then
  step "Dashboard: http://127.0.0.1:7357"
  exec "${WHY[@]}" serve
fi
