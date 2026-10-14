#!/usr/bin/env python3
"""Build "larkspur-web": the TypeScript twin of the Larkspur demo (Vitest, npm).

Same fictional company and decisions as demo/build_demo.py, so Why's JS path (code index,
Vitest guards, npm upgrade tracing) is exercised the same way the Python path is. It depends
on the fictional npm package `chronokit` (1.4.0 returns UTC ISO strings; 2.0.0 keeps the
wall-clock time and drops the offset), installed from local tarballs.

    python demo/build_demo_js.py [dest]     # default: demo/out/larkspur-web  (runs npm install)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE / "out" / "larkspur-web"
NPM_PKGS = HERE / "packages" / "npm"
NOW = datetime.now(timezone.utc).replace(microsecond=0)

CHRONOKIT = {
    "1.4.0": '''/** chronokit 1.4.0 (fictional): ISO-8601 parsing that normalises to UTC. */
export function parse(text) {
  return new Date(text).toISOString();
}
''',
    "2.0.0": '''/** chronokit 2.0.0 (fictional). Changelog: "parse() now preserves the wall-clock time written in
 * the input, which is simpler for UI code." Same signature. */
export function parse(text) {
  return text.replace(/(Z|[+-]\\d\\d:?\\d\\d)$/, "");
}
''',
}

FILES = {
    "package.json": json.dumps({
        "name": "larkspur-web", "private": True, "type": "module",
        "scripts": {"test": "vitest run"},
        "dependencies": {"chronokit": "file:vendor/chronokit-1.4.0.tgz"},
        "devDependencies": {"vitest": "5.0.2", "typescript": "5.9.3"},
    }, indent=2) + "\n",
    ".gitignore": "node_modules/\n",
    "README.md": "# Larkspur web\n\nThe storefront's TypeScript services. Payments via Quillpay, SMS via Fernwave.\n",
    "src/orders.ts": '''export type Order = { id: string; totalCents: number; status: string };

export const reconcileQueue: string[] = [];

export function enqueueReconciliation(order: Order): void {
  reconcileQueue.push(order.id);
}
''',
    "src/payments/gateway.ts": '''const QUILLPAY_URL = "https://api.quillpay.example/v2/charges";

export async function charge(amountCents: number, cardToken: string, idempotencyKey: string) {
  const res = await fetch(QUILLPAY_URL, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ amount: amountCents, source: cardToken }),
    signal: AbortSignal.timeout(10_000),
  });
  return res.json();
}
''',
    "src/pricing.ts": '''export type Cart = { items: { priceCents: number; quantity: number }[] };

export function cartTotal(cart: Cart): number {
  return cart.items.reduce((sum, i) => sum + Math.round(i.priceCents) * i.quantity, 0);
}
''',
    "src/checkout.ts": '''import { enqueueReconciliation, type Order } from "./orders";
import { charge } from "./payments/gateway";
import { cartTotal, type Cart } from "./pricing";

export async function placeOrder(cart: Cart, customer: { cardToken: string }): Promise<Order> {
  const order: Order = { id: crypto.randomUUID().slice(0, 10), totalCents: cartTotal(cart), status: "new" };
  try {
    await charge(order.totalCents, customer.cardToken, order.id);
  } catch (err) {
    // Do not retry: Quillpay may already have captured the payment.
    order.status = "payment_pending";
    enqueueReconciliation(order);
    return order;
  }
  order.status = "paid";
  return order;
}
''',
    "src/dates.ts": '''import { parse } from "chronokit";

/** Parse a timestamp from a client or partner feed. */
export function parseDate(text: string): string {
  return parse(text);
}
''',
    "src/pricing.test.ts": '''import { expect, test } from "vitest";
import { cartTotal } from "./pricing";

test("cart total", () => {
  expect(cartTotal({ items: [{ priceCents: 499, quantity: 2 }, { priceCents: 1250, quantity: 1 }] })).toBe(2248);
});
''',
}

HISTORY = [
    (60, "Maya Okafor", "Initial storefront services",
     ["package.json", ".gitignore", "README.md", "src/orders.ts", "src/pricing.ts", "src/pricing.test.ts"]),
    (52, "Maya Okafor", "Add Quillpay gateway and checkout", ["src/payments/gateway.ts", "src/checkout.ts"]),
    (40, "agent: checkout-session", "Checkout: handle gateway timeouts without retrying (#42)\n\n"
     "Never retry `charge()` on a timeout: the gateway double-bills on retry, because the first request\n"
     "may already have captured the payment. Mark the order payment_pending and reconcile.", []),
    (33, "Dev Raman", "Dates: normalise through chronokit (#51)\n\n"
     "`parseDate('2026-03-01T10:00:00+05:30')` must always return UTC because order timestamps are\n"
     "compared across regions.", ["src/dates.ts"]),
    (21, "agent: pricing-session", "Pricing: integer cents (#63)\n\n"
     "`cartTotal({ items: [{ priceCents: 199.5, quantity: 3 }] })` must always return an integer number\n"
     "of cents, never floats, because float drift broke the refund totals.", []),
]


def run(*args: str, cwd: Path = DEST, env: dict | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True, env={**os.environ, **(env or {})}, capture_output=True)


def build_packages() -> None:
    NPM_PKGS.mkdir(parents=True, exist_ok=True)
    for version, src in CHRONOKIT.items():
        tgz = NPM_PKGS / f"chronokit-{version}.tgz"
        if tgz.exists() and "index.cjs" in subprocess.run(["tar", "-tzf", str(tgz)], capture_output=True,
                                                          text=True).stdout:
            continue
        pkg = NPM_PKGS / f"src-chronokit-{version}"
        shutil.rmtree(pkg, ignore_errors=True)
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({
            "name": "chronokit", "version": version, "type": "module", "main": "index.cjs",
            "exports": {".": {"import": "./index.js", "require": "./index.cjs"}},
            "description": "Fictional date library for the Why demo"}, indent=2))
        (pkg / "index.js").write_text(src)
        # CommonJS build for Jest / require() users
        (pkg / "index.cjs").write_text(src.replace("export function parse", "function parse")
                                       + "\nmodule.exports = { parse };\n")
        run("npm", "pack", "--silent", "--pack-destination", str(NPM_PKGS), cwd=pkg)
        shutil.rmtree(pkg)


def main() -> None:
    build_packages()
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    (DEST / "vendor").mkdir()
    shutil.copy(NPM_PKGS / "chronokit-1.4.0.tgz", DEST / "vendor")
    FILES[".gitignore"] += ""
    run("git", "init", "-q", "-b", "main")
    for days, author, msg, files in HISTORY:
        for f in files:
            (DEST / f).parent.mkdir(parents=True, exist_ok=True)
            (DEST / f).write_text(FILES[f])
        when = (NOW - timedelta(days=days)).isoformat()
        email = author.lower().replace(" ", ".").replace(":", "") + "@larkspur.example"
        env = {"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": author,
               "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        run("git", "add", "-A")
        run("git", "commit", "-q", "--allow-empty", "-m", msg, env=env)
    install(DEST)
    print(DEST)


def install(dest: Path) -> None:
    """pnpm if available (npm 9 on some Node 22 setups crashes with "reading 'edgesOut'"), else npm."""
    if shutil.which("pnpm"):
        run("pnpm", "install", "--reporter=silent", cwd=dest)
    else:
        run("npm", "install", "--silent", "--no-audit", "--no-fund", cwd=dest)


if __name__ == "__main__":
    main()
