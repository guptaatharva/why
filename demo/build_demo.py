#!/usr/bin/env python3
"""Build the Why demo repo: "Larkspur", a fictional online storefront.

Creates a git repo with ~2 months of history in which the team (and their coding agents)
made real decisions in commit messages, PR descriptions and an agent session log.
Everything here is fictional: Larkspur, the Quillpay gateway, the Fernwave SMS vendor
and the chronokit library.

    python demo/build_demo.py [dest]      # default: demo/out/larkspur
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
DEST = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE / "out" / "larkspur"
NOW = datetime.now(timezone.utc).replace(microsecond=0)

FILES: dict[str, str] = {}

FILES["README.md"] = """# Larkspur storefront

A small online store for seeds and garden kits. Payments go through Quillpay, SMS
notifications through Fernwave. Most code is written by coding agents and reviewed by
two humans.
"""

FILES["requirements.txt"] = "chronokit==1.4.0\n"

FILES["larkspur/__init__.py"] = '"""Larkspur storefront."""\n'

FILES["larkspur/payments/__init__.py"] = ""

FILES["larkspur/payments/gateway.py"] = '''"""Quillpay gateway client."""

import json
import urllib.request

QUILLPAY_URL = "https://api.quillpay.example/v2/charges"


class PaymentDeclined(Exception):
    pass


def charge(amount_cents: int, card_token: str, idempotency_key: str) -> dict:
    """Charge a card once. Raises TimeoutError if Quillpay does not answer in 10s."""
    body = json.dumps({"amount": amount_cents, "source": card_token}).encode()
    req = urllib.request.Request(QUILLPAY_URL, data=body, headers={"Idempotency-Key": idempotency_key})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    if data.get("status") != "succeeded":
        raise PaymentDeclined(data.get("failure_message", "declined"))
    return data
'''

FILES["larkspur/orders.py"] = '''"""Order records and the reconciliation queue."""

from dataclasses import dataclass, field

RECONCILE_QUEUE: list[str] = []


@dataclass
class Order:
    id: str
    total_cents: int
    status: str = "new"
    events: list[str] = field(default_factory=list)


def enqueue_reconciliation(order: "Order") -> None:
    RECONCILE_QUEUE.append(order.id)
'''

FILES_V1_CHECKOUT = '''"""Checkout: turn a cart into a paid order."""

import logging
import uuid

from larkspur.orders import Order
from larkspur.payments.gateway import charge
from larkspur.pricing import cart_total

log = logging.getLogger(__name__)


def place_order(cart, customer) -> Order:
    total = cart_total(cart)
    order = Order(id=uuid.uuid4().hex[:10], total_cents=total)
    charge(total, customer.card_token, idempotency_key=order.id)
    order.status = "paid"
    return order
'''

FILES["larkspur/checkout.py"] = '''"""Checkout: turn a cart into a paid order."""

import logging
import uuid

from larkspur.orders import Order, enqueue_reconciliation
from larkspur.payments.gateway import charge
from larkspur.pricing import cart_total

log = logging.getLogger(__name__)


def place_order(cart, customer) -> Order:
    total = cart_total(cart)
    order = Order(id=uuid.uuid4().hex[:10], total_cents=total)
    try:
        charge(total, customer.card_token, idempotency_key=order.id)
    except TimeoutError:
        # Do not retry here: Quillpay may already have captured the payment.
        order.status = "payment_pending"
        enqueue_reconciliation(order)
        log.warning("charge timed out for order %s; queued for reconciliation", order.id)
        return order
    order.status = "paid"
    return order
'''

FILES["larkspur/pricing.py"] = '''"""Prices are integer cents everywhere."""


def cart_total(cart) -> int:
    return sum(int(item.price_cents) * int(item.quantity) for item in cart.items)


def apply_discount(total_cents: int, percent: int) -> int:
    return total_cents - (total_cents * percent) // 100
'''

FILES["larkspur/dates.py"] = '''"""All timestamps go through here."""

from datetime import datetime

import chronokit


def parse_date(text: str) -> datetime:
    """Parse an ISO-8601 timestamp from a client or partner feed."""
    return chronokit.parse(text)


def is_before(a: str, b: str) -> bool:
    return parse_date(a) < parse_date(b)
'''

FILES["larkspur/notify.py"] = '''"""Order notifications over Fernwave SMS."""

import logging
import time

log = logging.getLogger(__name__)

SMS_PER_MINUTE = 100


def send_batch(messages, send_one) -> int:
    sent = 0
    for i, msg in enumerate(messages):
        if i and i % SMS_PER_MINUTE == 0:
            time.sleep(60)
        send_one(msg)
        sent += 1
    log.info("sent %d sms", sent)
    return sent
'''

FILES["larkspur/reports.py"] = '''"""Daily sales report."""

import logging

from larkspur.dates import parse_date

log = logging.getLogger(__name__)


def daily_total(orders, day: str) -> int:
    d = parse_date(day).date()
    total = sum(o["total_cents"] for o in orders if parse_date(o["created_at"]).date() == d)
    log.info("daily total for %s: %d", day, total)
    return total
'''

FILES["tests/test_pricing.py"] = '''from types import SimpleNamespace as NS

from larkspur.pricing import apply_discount, cart_total


def test_cart_total():
    cart = NS(items=[NS(price_cents=499, quantity=2), NS(price_cents=1250, quantity=1)])
    assert cart_total(cart) == 2248


def test_discount():
    assert apply_discount(1000, 15) == 850
'''

SESSION = [
    {"type": "user", "timestamp": "", "message": {"role": "user", "content":
        "Refund totals were off by a cent again last quarter. Always compute prices in integer cents, never floats, "
        "because float rounding drift broke the refund totals. Can you fix cart_total and apply_discount?"}},
    {"type": "assistant", "timestamp": "", "message": {"role": "assistant", "content": [{"type": "text", "text":
        "Done. `cart_total()` and `apply_discount()` now work in integer cents end to end. I also noticed "
        "`place_order()` handles timeouts by queueing reconciliation; I left that alone."}]}},
    {"type": "user", "timestamp": "", "message": {"role": "user", "content":
        "Thanks. Also we should probably never deploy on Fridays lol."}},
]

# (days ago, author, message, files to write before committing)
HISTORY = [
    (62, "Maya Okafor", "Initial storefront skeleton",
     ["README.md", "requirements.txt", "larkspur/__init__.py", "larkspur/orders.py", "larkspur/pricing.py",
      "tests/test_pricing.py"]),
    (55, "Maya Okafor", "Add Quillpay gateway client and checkout",
     ["larkspur/payments/__init__.py", "larkspur/payments/gateway.py", ("larkspur/checkout.py", FILES_V1_CHECKOUT)]),
    (41, "agent: checkout-session", "Checkout: handle gateway timeouts without retrying (#42)\n\n"
     "Quillpay timed out on ~0.3% of charges last week and the naive fix was a retry loop.\n"
     "Never retry `charge()` on a timeout: the gateway double-bills on retry, because the first\n"
     "request may already have captured the payment. Mark the order payment_pending and let the\n"
     "reconciliation job settle it.\n\nReviewed-by: Dev Raman",
     ["larkspur/checkout.py"]),
    (34, "Dev Raman", "Dates: normalise everything through chronokit (#51)\n\n"
     "`parse_date('2026-03-01T10:00:00+05:30')` must always return UTC because order timestamps\n"
     "are compared across regions and stored without offsets. Partner feeds send local offsets.",
     ["larkspur/dates.py", "larkspur/reports.py"]),
    (26, "agent: notify-session", "Notifications: batch SMS sends (#57)\n\n"
     "We must send at most 100 SMS per minute: Fernwave's API rate limit is 100 requests per minute,\n"
     "and they suspend API keys that exceed it.",
     ["larkspur/notify.py"]),
    (19, "Maya Okafor", "Logging cleanup (#60)\n\n"
     "Don't call `print()` in larkspur/: everything goes through the logger so card tokens are scrubbed\n"
     "before logs leave the box.",
     []),
    (12, "agent: pricing-session", "Pricing: keep everything in integer cents (#63)", []),
    (5, "Dev Raman", "chore: tidy imports", []),
]


def run(*args: str, env: dict | None = None) -> None:
    subprocess.run(args, cwd=DEST, check=True, env={**os.environ, **(env or {})}, capture_output=True)


def write(rel: str, content: str) -> None:
    p = DEST / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    run("git", "init", "-q", "-b", "main")
    for days, author, msg, files in HISTORY:
        for f in files:
            if isinstance(f, tuple):
                write(*f)
            else:
                write(f, FILES[f])
        if days == 12:
            sess = DEST / ".why" / "sessions" / "pricing-session.jsonl"
            sess.parent.mkdir(parents=True, exist_ok=True)
            ts = (NOW - timedelta(days=days)).isoformat()
            sess.write_text("\n".join(json.dumps({**m, "timestamp": ts}) for m in SESSION) + "\n")
        when = (NOW - timedelta(days=days)).isoformat()
        name = author
        email = author.lower().replace(" ", ".").replace(":", "") + "@larkspur.example"
        env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
               "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        run("git", "add", "-A")
        run("git", "commit", "-q", "--allow-empty", "-m", msg, env=env)
    print(DEST)


if __name__ == "__main__":
    main()
