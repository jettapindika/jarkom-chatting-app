"""The client's reader thread: receive, dispatch, and detect a dead server.

Why a thread and not an event loop
----------------------------------
The CLI's main thread is parked in ``input()``, which cannot be interrupted and
does not return until the user presses Enter. Something else therefore has to
own the socket, and a thread is the smallest thing that can. The two directions
are genuinely independent -- the reader blocks in ``recv``, the main thread
blocks on the keyboard -- so they need no coordination beyond the channel's own
send lock.

Liveness
--------
TCP will not report a peer that vanished without closing; the socket stays open
and writable for around two hours. The reader therefore polls with a timeout and
uses the session's heartbeat monitor to tell "quiet" from "gone": it sends a PING
after an interval of silence, and gives up once the peer has been silent for the
whole timeout. That is why the poll timeout is derived from the policy instead of
being a magic number -- the two must agree or the deadline is enforced late.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from presentation import MessageType, make_message
from session.client_session import ClientSession
from session.heartbeat import HeartbeatPolicy
from transport import ConnectionClosedError, TransportTimeout

__all__ = ["Receiver"]


class Receiver(threading.Thread):
    """Reads inbound messages until the session ends.

    Args:
        session: the connected session to read from.
        on_message: called on this thread with each decoded envelope. It must
            return promptly -- the reader is the only thing draining the socket,
            so a slow callback is a slow reader.
        on_finish: called once when the loop ends, with a human-readable reason,
            or ``None`` if the client itself asked to stop.
        policy: heartbeat timing. Defaults to the shared policy, so client and
            server agree on when a peer is dead without either hardcoding the
            other's number.
        stop_event: set by the owner to ask this thread to stop. The reader also
            sets it, so the input loop can notice the session ended.
    """

    def __init__(
        self,
        session: ClientSession,
        *,
        on_message: Callable[[dict[str, Any]], None],
        on_finish: Callable[[str | None], None] | None = None,
        policy: HeartbeatPolicy | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        super().__init__(name="chat-receiver", daemon=True)
        self._session = session
        self._on_message = on_message
        self._on_finish = on_finish
        self._policy = policy if policy is not None else HeartbeatPolicy()
        self._stop = stop_event if stop_event is not None else threading.Event()
        # Poll at the ping interval: it is exactly how often a decision is due,
        # so the deadline is enforced on time without a timer of its own.
        self._poll_timeout = self._policy.interval

    @property
    def stop_event(self) -> threading.Event:
        """The event that, once set, ends both this thread and the input loop."""
        return self._stop

    def stop(self) -> None:
        """Ask the thread to stop. Safe to call from any thread."""
        self._stop.set()

    def run(self) -> None:
        """Read until the peer goes away, the socket fails, or ``stop`` is set."""
        reason: str | None = None
        try:
            while not self._stop.is_set():
                try:
                    _, message = self._session.receive(timeout=self._poll_timeout)
                except TransportTimeout:
                    # Silence, not failure. Either the server is merely quiet, or
                    # it is gone; the monitor is what tells the two apart, and it
                    # has been counting since the last inbound frame.
                    if self._session.core.heartbeat.peer_expired:
                        reason = "server tidak merespons (heartbeat timeout)"
                        break
                    if self._session.core.heartbeat.ping_due():
                        self._probe()
                    continue

                self._on_message(message)
        except ConnectionClosedError:
            reason = "koneksi ke server ditutup"
        except OSError as exc:
            reason = f"koneksi bermasalah: {exc}"
        except Exception as exc:  # noqa: BLE001 - the reader must not die silently
            reason = f"penerima berhenti karena error: {exc}"
        finally:
            # Always signal completion. The main thread is parked in input() and
            # has no other way to learn that the session is over.
            self._stop.set()
            if self._on_finish is not None:
                self._on_finish(reason)

    def _probe(self) -> None:
        """Send one PING.

        A failure here is not retried: the socket is the only channel there is,
        and it has just refused to carry a frame. The next read will surface the
        same condition as a closed connection, which is where it is handled.
        """
        self._session.send(
            make_message(MessageType.PING, {}, sender=self._session.nickname),
            summary="PING (heartbeat probe)",
        )
