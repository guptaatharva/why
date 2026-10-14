"""What a coding agent writes when asked to "fix the checkout timeouts": a retry loop."""
from pathlib import Path
import sys

p = Path(sys.argv[1]) / "larkspur" / "checkout.py"
p.write_text('''"""Checkout: turn a cart into a paid order."""

import logging
import time
import uuid

from larkspur.orders import Order
from larkspur.payments.gateway import charge
from larkspur.pricing import cart_total

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def place_order(cart, customer) -> Order:
    total = cart_total(cart)
    order = Order(id=uuid.uuid4().hex[:10], total_cents=total)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            charge(total, customer.card_token, idempotency_key=f"{order.id}-{attempt}")
            break
        except TimeoutError:
            if attempt == MAX_ATTEMPTS:
                raise
            log.warning("charge timed out, retrying (%d/%d)", attempt, MAX_ATTEMPTS)
            time.sleep(0.5 * attempt)
    order.status = "paid"
    return order
''')
