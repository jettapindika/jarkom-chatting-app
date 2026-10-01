"""Opening a session: connect the socket, run the handshake, explain failures.

Everything that can go wrong before the first chat message is turned into one
exception carrying a message meant for a person. A traceback is the wrong answer
to "the server is not running yet": the user cannot act on ``ConnectionRefusedError``
from a stack trace, but they can act on being told the address and that nothing
is listening there.

The layer errors are still chained, so ``--trace`` and a debugger keep the detail.
"""

from __future__ import annotations

import socket

from presentation import ErrorCode
from session.client_session import ClientSession
from session.errors import HandshakeError
from trace import TraceEmitter
from transport import (
    DEFAULT_CONNECT_TIMEOUT,
    BlockingTcpChannel,
    ConnectionClosedError,
    TransportTimeout,
)

__all__ = ["ConnectFailure", "open_session"]


class ConnectFailure(Exception):
    """A session could not be established, with a message written for the user."""


#: Handshake rejections, phrased as what the user should do about them. A dict
#: rather than a chain of comparisons so the coverage is visible at a glance --
#: a missing code is obvious here, and silently absent in an ``if`` ladder.
_REJECTION_ADVICE = {
    ErrorCode.NICK_TAKEN: "nickname sudah dipakai user lain, coba nickname lain",
    ErrorCode.NICK_INVALID: "nickname tidak valid (huruf, angka, '-', '_'; maksimal 24 karakter)",
    ErrorCode.PROTOCOL_MISMATCH: "versi protokol client tidak cocok dengan server",
    ErrorCode.SERVER_FULL: "server penuh, coba lagi nanti",
    ErrorCode.MALFORMED: "server tidak dapat membaca pesan CONNECT dari client ini",
}


def open_session(
    host: str,
    port: int,
    nickname: str,
    *,
    emitter: TraceEmitter | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> ClientSession:
    """Connect to ``host:port`` and complete the CONNECT handshake.

    Args:
        host: server address, supplied by the caller -- never hardcoded here.
        port: server port.
        nickname: the nickname to request.
        emitter: trace emitter shared with the channel and the session, so one
            message's events line up across L4, L5 and L7.
        connect_timeout: seconds allowed for the TCP connect itself.

    Returns:
        A session that has completed its handshake.

    Raises:
        ConnectFailure: for every failure mode, with a human-readable message.
    """
    address = f"{host}:{port}"

    try:
        channel = BlockingTcpChannel.connect(
            host, port, emitter=emitter, connect_timeout=connect_timeout
        )
    except socket.gaierror as exc:
        raise ConnectFailure(f"nama host tidak dapat diterjemahkan: {host!r}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ConnectFailure(
            f"timeout menghubungi {address} setelah {connect_timeout:g} detik"
        ) from exc
    except ConnectionRefusedError as exc:
        raise ConnectFailure(
            f"koneksi ke {address} ditolak -- pastikan server sudah berjalan"
        ) from exc
    except OSError as exc:
        raise ConnectFailure(f"gagal terhubung ke {address}: {exc}") from exc

    session = ClientSession(channel, nickname=nickname, emitter=emitter)
    try:
        session.connect()
    except HandshakeError as exc:
        channel.close()
        raise ConnectFailure(_explain(exc)) from exc
    except (ConnectionClosedError, TransportTimeout, OSError) as exc:
        channel.close()
        raise ConnectFailure(
            f"koneksi ke {address} terputus saat handshake: {exc}"
        ) from exc

    return session


def _explain(exc: HandshakeError) -> str:
    """Turn a handshake rejection into advice.

    The server sends a machine-readable code inside the message; this only
    decides how to phrase it. An unrecognised code falls through to the raw
    text rather than being swallowed, so a code added later stays visible.
    """
    message = str(exc)
    for code, advice in _REJECTION_ADVICE.items():
        if code.value in message:
            return advice
    return f"handshake ditolak server: {message}"
