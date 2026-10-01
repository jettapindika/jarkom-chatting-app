"""Heartbeat policy: detecting a peer that has stopped answering.

Why this exists at all
----------------------
TCP will not tell you promptly that a peer is gone. If a machine loses power or
its network drops, the socket stays open and writable from the local point of
view, and the kernel will not probe it for roughly two hours by default. In a
demo -- and in any real chat -- that is indistinguishable from "no messages
arrived". So liveness is asserted at the application layer, where the timing is
ours to choose and the detection is visible in the trace.

The trade-off, stated plainly: this is the protocol's own mechanism and it
costs one PING/PONG round trip every ``interval`` seconds per idle connection.
``SO_KEEPALIVE`` would cost nothing per message but is far too slow to be
useful, and its thresholds are not portably configurable across platforms.

This module is pure logic -- no sockets, no clock of its own -- so the timing
rules can be unit-tested by advancing a fake clock instead of sleeping.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL",
    "DEFAULT_HEARTBEAT_TIMEOUT",
    "HeartbeatMonitor",
    "HeartbeatPolicy",
]

#: Seconds of outbound silence before sending a PING.
DEFAULT_HEARTBEAT_INTERVAL = 15.0

#: Seconds without any inbound traffic before declaring the peer dead. Set to
#: three times the interval so that two consecutive PINGs may be lost before the
#: connection is torn down.
DEFAULT_HEARTBEAT_TIMEOUT = 45.0


@dataclass(frozen=True, slots=True)
class HeartbeatPolicy:
    """When to probe, and when to give up."""

    interval: float = DEFAULT_HEARTBEAT_INTERVAL
    timeout: float = DEFAULT_HEARTBEAT_TIMEOUT

    def __post_init__(self) -> None:
        if self.interval <= 0:
            raise ValueError("interval must be positive")
        if self.timeout <= self.interval:
            raise ValueError("timeout must be greater than interval")

    def should_ping(self, idle_seconds: float) -> bool:
        """Whether a PING is due after ``idle_seconds`` of outbound silence."""
        return idle_seconds >= self.interval

    def has_expired(self, silent_seconds: float) -> bool:
        """Whether ``silent_seconds`` without inbound traffic means the peer is gone."""
        return silent_seconds >= self.timeout


class HeartbeatMonitor:
    """Tracks the last time the peer was heard from and answers probe questions.

    One monitor per session. ``clock`` is injectable so tests can drive it
    deterministically rather than sleeping.
    """

    __slots__ = ("_last_inbound", "_last_outbound", "_policy", "_clock")

    def __init__(
        self,
        policy: HeartbeatPolicy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy if policy is not None else HeartbeatPolicy()
        self._clock = clock
        now = clock()
        self._last_inbound = now
        self._last_outbound = now

    @property
    def policy(self) -> HeartbeatPolicy:
        """The timing rules in force."""
        return self._policy

    @property
    def silent_seconds(self) -> float:
        """Seconds since any inbound traffic arrived."""
        return self._clock() - self._last_inbound

    @property
    def idle_seconds(self) -> float:
        """Seconds since any outbound traffic was sent."""
        return self._clock() - self._last_outbound

    def touch_inbound(self) -> None:
        """Record inbound traffic, resetting the liveness deadline."""
        self._last_inbound = self._clock()

    def touch_outbound(self) -> None:
        """Record outbound traffic, resetting the PING timer."""
        self._last_outbound = self._clock()

    def ping_due(self) -> bool:
        """Whether outbound silence has reached the ping interval."""
        return self._policy.should_ping(self.idle_seconds)

    def peer_expired(self) -> bool:
        """Whether the peer has been silent past the timeout."""
        return self._policy.has_expired(self.silent_seconds)
