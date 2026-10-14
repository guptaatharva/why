"""Why guard for D-0005: Send at most 100 SMS per minute (hand-written by Dev Raman).

Reason: Fernwave's API rate limit is 100 requests per minute, and they suspend keys that exceed it.
"""

from unittest import mock

from larkspur import notify


def test_d0005_send_at_most_100_sms_per_minute():
    sent_at, clock = [], [0.0]

    def fake_sleep(s):
        clock[0] += s

    with mock.patch.object(notify.time, "sleep", fake_sleep):
        notify.send_batch(range(250), lambda m: sent_at.append(clock[0]))

    worst = max(sum(1 for t in sent_at if start <= t < start + 60) for start in sent_at)
    assert worst <= 100, f"{worst} SMS sent within one minute. Violates D-0005: Fernwave suspends keys over 100/min."
