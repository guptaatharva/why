#!/usr/bin/env python3
"""Two agents in parallel worktrees (F7 demo).

agent/refunds adds refunds and records "refunds never exceed the amount charged" (with a guard).
agent/promo, branched from main at the same time, turns discounts into a promo surcharge.
Different files, so git merges them cleanly; each branch passes alone. Together, refunds exceed charges.

    python demo/parallel_agents.py <repo>      (uses the `why` on PATH, or WHY_BIN)
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
WHY = shlex.split(os.environ.get("WHY_BIN", "why"))
GIT = ["git", "-c", "user.name=agent", "-c", "user.email=agent@larkspur.example"]


def sh(*args, **kw):
    return subprocess.run(args, cwd=REPO, check=True, capture_output=True, text=True, **kw).stdout


sh(*GIT, "checkout", "-q", "-b", "agent/refunds", "main")
(REPO / "larkspur" / "refunds.py").write_text(
    '"""Refunds."""\n\nfrom larkspur.pricing import apply_discount\n\n\n'
    "def refund_amount(total_cents: int, discount_percent: int) -> int:\n"
    '    """What we refund for an order that used a discount."""\n'
    "    return apply_discount(total_cents, discount_percent)\n")
out = sh(*WHY, "add", "Refunds never exceed the amount charged",
         "--reason", "the gateway rejects over-refunds and flags the merchant account",
         "--kind", "returns", "--params", json.dumps({"target": "larkspur.refunds:refund_amount",
                                                     "call": "refund_amount(1000, 15)",
                                                     "predicate": "0 <= result <= 1000"}),
         "--symbol", "larkspur.refunds:refund_amount", "--file", "larkspur/refunds.py")
did = out.split()[1]
sh(*WHY, "approve", did)
sh(*GIT, "add", "-A")
sh(*GIT, "commit", "-qm", f"Refunds: add refund_amount and decision {did}")

sh(*GIT, "checkout", "-q", "-b", "agent/promo", "main")
p = REPO / "larkspur" / "pricing.py"
p.write_text(p.read_text().replace("return total_cents - (total_cents * percent) // 100",
                                   "return total_cents + (total_cents * percent) // 100  # promo: percent is a surcharge"))
sh(*GIT, "commit", "-qam", "Promo pricing: treat percent as a surcharge for the launch bundle")
sh(*GIT, "checkout", "-q", "main")
print(f"agent/refunds (adds {did}) and agent/promo created from main")
