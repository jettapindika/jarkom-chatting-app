"""The CLI reader thread, driven against a fake session.

``Receiver`` is the only production consumer of the heartbeat monitor on the
client side, and it runs on its own thread -- which makes it exactly the kind
of code where a bound-method typo (``peer_expired`` instead of ``peer_expired()``)
can read as "always expired" for months. The fake session below replaces the
socket entirely, so the tests here drive the loop's decisions, not the network.
"""

from __future__ import annotations

import threading
import time
import unittest
from typing import Any

from client.receiver import Receiver
from presentation import MessageType, make_message
from session.heartbeat import HeartbeatPolicy
from transport.errors import TransportTimeout


class FakeSession:
    """Stands in for ``ClientSession`` with scriptable silence and liveness.

    ``receive`` raises :class:`TransportTimeout` on every call -- a permanently
    quiet server -- while ``core.heartbeat`` is a real monitor whose clock the
    test controls by simply sleeping. ``send`` records probes so the tests can
    assert on the PING behaviour without a socket.
    """

    def __init__(self, *, interval: float, timeout: float) -> None:
        self.nickname = "budi"
        from session.session import SessionCore

        self.core = SessionCore(heartbeat=HeartbeatPolicy(interval=interval, timeout=timeout))
        self.core.open("11111111-1111-1111-1111-111111111111")
        self.probes: list[str] = []

    def receive(self, *, timeout: float | None = None) -> tuple[Any, dict[str, Any]]:
        raise TransportTimeout(timeout or 0.0)

    def send(self, message: dict[str, Any], **_: Any) -> None:
        self.probes.append(str(message["type"]))


class ReceiverTests(unittest.TestCase):
    """Silence, probing, and expiry -- decided without a network."""

    INTERVAL = 0.05
    TIMEOUT = 0.2

    def make_receiver(
        self, session: FakeSession
    ) -> tuple[Receiver, threading.Event, list[str | None]]:
        reasons: list[str | None] = []
        finished = threading.Event()

        receiver = Receiver(
            session,  # type: ignore[arg-type]
            on_message=lambda message: None,
            on_finish=lambda reason: (reasons.append(reason), finished.set()),
            policy=HeartbeatPolicy(interval=self.INTERVAL, timeout=self.TIMEOUT),
        )
        return receiver, finished, reasons

    def test_quiet_server_is_probed_and_the_reader_survives(self) -> None:
        """Regression: a bound-method ``peer_expired`` read as always-expired.

        Pre-fix, the very first quiet poll ended the session with a bogus
        heartbeat timeout. Post-fix, quiet means PING: the reader stays alive
        past several poll intervals and keeps probing.
        """
        session = FakeSession(interval=self.INTERVAL, timeout=self.TIMEOUT)
        receiver, finished, _ = self.make_receiver(session)
        receiver.start()

        # Several poll intervals of silence, still under the expiry deadline.
        self.assertFalse(
            finished.wait(self.TIMEOUT * 0.8),
            "reader stopped while the server was merely quiet",
        )
        receiver.stop()
        receiver.join(timeout=5)
        self.assertEqual(session.probes[0], "PING")
        self.assertGreaterEqual(len(session.probes), 1)

    def test_silent_server_expires_the_reader_with_a_reason(self) -> None:
        """Past the timeout with no inbound traffic, the reader gives up."""
        session = FakeSession(interval=self.INTERVAL, timeout=self.TIMEOUT)
        receiver, finished, reasons = self.make_receiver(session)
        receiver.start()

        self.assertTrue(finished.wait(self.TIMEOUT * 4), "reader never expired")
        self.assertIn("heartbeat", str(reasons[0]))
        # It probed before giving up -- twice, at the policy's cadence.
        self.assertGreaterEqual(len(session.probes), 1)
        receiver.join(timeout=5)

    def test_stop_is_reported_as_a_clean_exit(self) -> None:
        session = FakeSession(interval=self.INTERVAL, timeout=self.TIMEOUT)
        receiver, finished, reasons = self.make_receiver(session)
        receiver.start()
        time.sleep(self.INTERVAL)

        receiver.stop()
        self.assertTrue(finished.wait(2.0))
        self.assertIsNone(reasons[0])


if __name__ == "__main__":
    unittest.main()
