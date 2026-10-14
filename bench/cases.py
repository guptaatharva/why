"""BBC-style micro benchmark: behavioural dependency breaks that keep the library's API.

These cases are written for Why's own regression testing and are modelled on the break
patterns described for BBCBench (arXiv 2610.09267): same signature, different runtime
behaviour. They are NOT BBCBench itself; see bench/README.md for running the real set.

Each case: a tiny library at an old and a new version, a client app using it, and the
decision the client team recorded. `expect_break` False marks control upgrades that must
not be blocked (false-positive check).
"""

CASES = [
    {
        "name": "tz-naive-return",
        "lib": "tzlite",
        "old": "from datetime import datetime, timezone\n\ndef parse(s):\n    return datetime.fromisoformat(s).astimezone(timezone.utc)\n",
        "new": "from datetime import datetime\n\ndef parse(s):\n    return datetime.fromisoformat(s).replace(tzinfo=None)\n",
        "app": "import tzlite\n\ndef created_at(s):\n    return tzlite.parse(s)\n",
        "decision": {"title": "created_at() always returns UTC", "reason": "rows are compared across regions",
                     "kind": "returns", "params": {"target": "app.core:created_at",
                                                   "call": "created_at('2026-01-01T10:00:00+02:00')",
                                                   "predicate": "result.utcoffset().total_seconds() == 0"}},
        "expect_break": True, "expect_api": "tzlite.parse",
    },
    {
        "name": "rounding-mode",
        "lib": "moneyfmt",
        "old": "from decimal import Decimal, ROUND_HALF_UP\n\ndef round_cents(x):\n    return int(Decimal(str(x)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))\n",
        "new": "def round_cents(x):\n    return round(x)  # banker's rounding\n",
        "app": "import moneyfmt\n\ndef fee(amount_cents):\n    return moneyfmt.round_cents(amount_cents * 0.025)\n",
        "decision": {"title": "fee() rounds half up", "reason": "matches the card network's statement",
                     "kind": "returns", "params": {"target": "app.core:fee", "call": "fee(100)",
                                                   "predicate": "result == 3"}},
        "expect_break": True, "expect_api": "moneyfmt.round_cents",
    },
    {
        "name": "none-instead-of-empty",
        "lib": "tagstore",
        "old": "def tags_for(item):\n    return list(item.get('tags', []))\n",
        "new": "def tags_for(item):\n    return item.get('tags') or None\n",
        "app": "import tagstore\n\ndef labels(item):\n    return tagstore.tags_for(item)\n",
        "decision": {"title": "labels() never returns None", "reason": "templates iterate it directly",
                     "kind": "returns", "params": {"target": "app.core:labels", "call": "labels({})",
                                                   "predicate": "result is not None"}},
        "expect_break": True, "expect_api": "tagstore.tags_for",
    },
    {
        "name": "unstable-sort",
        "lib": "ranker",
        "old": "def rank(rows):\n    return sorted(rows, key=lambda r: -r[1])\n",
        "new": "def rank(rows):\n    return sorted(rows, key=lambda r: r[1])  # now ascending by default\n",
        "app": "import ranker\n\ndef leaderboard(rows):\n    return [r[1] for r in ranker.rank(rows)]\n",
        "decision": {"title": "leaderboard() is sorted high to low", "reason": "page shows top sellers first",
                     "kind": "returns", "params": {"target": "app.core:leaderboard",
                                                   "call": "leaderboard([('a', 1), ('b', 3), ('c', 2)])",
                                                   "predicate": "list(result) == sorted(result, reverse=True)"}},
        "expect_break": True, "expect_api": "ranker.rank",
    },
    {
        "name": "retry-default-on",
        "lib": "httplite",
        "old": "def post(url, body, send, retries=0):\n    for _ in range(retries + 1):\n        try:\n            return send(url, body)\n        except TimeoutError:\n            continue\n    raise TimeoutError(url)\n",
        "new": "def post(url, body, send, retries=2):\n    for _ in range(retries + 1):\n        try:\n            return send(url, body)\n        except TimeoutError:\n            continue\n    raise TimeoutError(url)\n",
        "app": "import httplite\n\ndef transmit(body):\n    return httplite.post('https://pay.example/charge', body, send_charge)\n\n\ndef send_charge(url, body):\n    raise NotImplementedError\n\n\ndef pay(body):\n    return transmit(body)\n",
        "decision": {"title": "Never retry send_charge()", "reason": "the processor double-charges",
                     "kind": "call_count", "params": {"target": "app.core:send_charge", "entry": "app.core:pay",
                                                      "raise": "TimeoutError", "max_calls": 1}},
        "expect_break": True, "expect_api": "httplite.post",
    },
    {
        "name": "case-folding",
        "lib": "slugger",
        "old": "def slug(s):\n    return '-'.join(s.lower().split())\n",
        "new": "def slug(s):\n    return '-'.join(s.split())\n",
        "app": "import slugger\n\ndef url_for(title):\n    return '/p/' + slugger.slug(title)\n",
        "decision": {"title": "url_for() is lowercase", "reason": "old links are lowercase; SEO",
                     "kind": "returns", "params": {"target": "app.core:url_for", "call": "url_for('Red Tulip')",
                                                   "predicate": "result == result.lower()"}},
        "expect_break": True, "expect_api": "slugger.slug",
    },
    {
        "name": "control-perf-only",
        "lib": "fastsum",
        "old": "def total(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t\n",
        "new": "def total(xs):\n    return sum(xs)\n",
        "app": "import fastsum\n\ndef order_total(items):\n    return fastsum.total(items)\n",
        "decision": {"title": "order_total() is exact", "reason": "integers only",
                     "kind": "returns", "params": {"target": "app.core:order_total", "call": "order_total([1, 2, 3])",
                                                   "predicate": "result == 6"}},
        "expect_break": False, "expect_api": None,
    },
    {
        "name": "control-new-optional-arg",
        "lib": "greet",
        "old": "def hello(name):\n    return f'Hello, {name}'\n",
        "new": "def hello(name, punct=''):\n    return f'Hello, {name}{punct}'\n",
        "app": "import greet\n\ndef banner(name):\n    return greet.hello(name)\n",
        "decision": {"title": "banner() has no trailing punctuation", "reason": "design spec",
                     "kind": "returns", "params": {"target": "app.core:banner", "call": "banner('Ana')",
                                                   "predicate": "result == 'Hello, Ana'"}},
        "expect_break": False, "expect_api": None,
    },
]
