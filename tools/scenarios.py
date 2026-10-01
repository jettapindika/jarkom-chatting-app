"""Run the manual test scenarios end to end and print a table of results.

This is the harness behind docs/LAPORAN_PENGUJIAN.md section 4. Every scenario
drives the real client stack against a real server; nothing is mocked. Start a
server first, then point this at it.

    python run_server.py --port 9009
    python tools/scenarios.py --port 9009
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time

sys.path.insert(0, ".")

from app.config import DEFAULT_HOST  # noqa: E402
from presentation import MessageType, encode, make_message  # noqa: E402
from session import SESSION_HEADER_SIZE, UNASSIGNED_SESSION_ID, encode_envelope  # noqa: E402
from session.client_session import ClientSession  # noqa: E402
from transport import BlockingTcpChannel, TransportTimeout, encode_frame  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, passed: bool, detail: str) -> None:
    RESULTS.append((name, "LULUS" if passed else "GAGAL", detail))
    print(f"  [{'LULUS' if passed else 'GAGAL'}] {name}: {detail}")


def connect(host: str, port: int, nick: str) -> ClientSession:
    channel = BlockingTcpChannel.connect(host, port)
    session = ClientSession(channel, nickname=nick)
    session.connect()
    return session


def drain(session: ClientSession, seconds: float) -> list[dict]:
    """Collect every message that arrives within the window."""
    seen: list[dict] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            _header, message = session.receive(timeout=0.3)
        except TransportTimeout:
            continue
        seen.append(message)
    return seen


def raw_connect(host: str, port: int, nick: str) -> tuple[socket.socket, dict]:
    """Handshake by hand, so a scenario can send bytes the client stack never would."""
    sock = socket.create_connection((host, port), timeout=5)
    message = make_message(MessageType.CONNECT, sender=nick, payload={"nick": nick})
    sock.sendall(
        encode_frame(encode_envelope(encode(message), session_id=UNASSIGNED_SESSION_ID, sequence=0))
    )
    reply = read_frame(sock)
    return sock, reply


def read_frame(sock: socket.socket) -> dict:
    head = sock.recv(4)
    if len(head) < 4:
        raise ConnectionError("no frame header")
    (size,) = struct.unpack(">I", head)
    body = b""
    while len(body) < size:
        chunk = sock.recv(size - len(body))
        if not chunk:
            raise ConnectionError("short frame")
        body += chunk
    return json.loads(body[SESSION_HEADER_SIZE:].decode())


def scenario_broadcast(host: str, port: int) -> None:
    print("\n1. Tiga klien bersamaan, broadcast saling terlihat")
    sessions = [connect(host, port, f"tiga{i}") for i in range(3)]
    try:
        sessions[0].send(make_message(MessageType.BROADCAST, {"text": "halo semua"}, sender="tiga0"))
        got = [drain(s, 1.2) for s in sessions]
        receivers = sum(
            1
            for seen in got
            if any(m["type"] is MessageType.BROADCAST and m["payload"].get("text") == "halo semua" for m in seen)
        )
        record("broadcast ke 3 klien", receivers == 3, f"{receivers}/3 klien menerima")
    finally:
        for s in sessions:
            s.close()


def scenario_private(host: str, port: int) -> None:
    print("\n2. Pesan pribadi /msg ke user yang ada dan yang tidak ada")
    a = connect(host, port, "privatA")
    b = connect(host, port, "privatB")
    c = connect(host, port, "privatC")
    try:
        drain(a, 0.6), drain(b, 0.6), drain(c, 0.6)
        a.send(make_message(MessageType.PRIVATE, {"text": "khusus B", "to": "privatB"}, sender="privatA"))
        seen_b = drain(b, 1.2)
        seen_c = drain(c, 1.2)
        b_got = any(m["type"] is MessageType.PRIVATE and m["payload"].get("text") == "khusus B" for m in seen_b)
        c_got = any(m["type"] is MessageType.PRIVATE for m in seen_c)
        record("private sampai ke tujuan", b_got, "privatB menerima")
        record("private tidak bocor ke pihak ketiga", not c_got, "privatC tidak menerima")

        a.send(make_message(MessageType.PRIVATE, {"text": "hai", "to": "hantu"}, sender="privatA"))
        seen_a = drain(a, 1.2)
        code = next(
            (m["payload"].get("code") for m in seen_a if m["type"] is MessageType.ERROR), None
        )
        record("private ke user tidak ada ditolak", code == "NO_SUCH_USER", f"kode error {code}")
    finally:
        for s in (a, b, c):
            s.close()


def scenario_duplicate_nick(host: str, port: int) -> None:
    print("\n3. Nickname duplikat ditolak")
    first = connect(host, port, "kembar")
    try:
        try:
            connect(host, port, "kembar")
            record("nickname duplikat ditolak", False, "koneksi kedua justru diterima")
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            record("nickname duplikat ditolak", "NICK_TAKEN" in str(exc) or "kembar" in str(exc), detail)
    finally:
        first.close()


def scenario_large_message(host: str, port: int) -> None:
    print("\n4. Pesan besar")
    a = connect(host, port, "besara")
    b = connect(host, port, "besarb")
    try:
        drain(a, 0.5), drain(b, 0.5)
        text = "x" * 4000
        a.send(make_message(MessageType.BROADCAST, {"text": text}, sender="besara"))
        seen = drain(b, 1.5)
        delivered = next(
            (m for m in seen if m["type"] is MessageType.BROADCAST and m["payload"].get("text") == text),
            None,
        )
        record("pesan 4000 karakter utuh", delivered is not None, f"diterima {len(delivered['payload']['text']) if delivered else 0} karakter")
    finally:
        a.close(), b.close()


def scenario_rename(host: str, port: int) -> None:
    print("\n5. Ganti nickname (/nick)")
    a = connect(host, port, "lama")
    b = connect(host, port, "pengamat")
    try:
        drain(a, 0.6), drain(b, 0.6)
        a.send(make_message(MessageType.NICK, {"nick": "baru"}, sender="lama"))
        seen_b = drain(b, 1.5)
        kinds = [m["type"] for m in seen_b]
        leave = MessageType.USER_LEAVE in kinds
        join = MessageType.USER_JOIN in kinds
        record(
            "rename menghasilkan USER_LEAVE lalu USER_JOIN",
            leave and join,
            f"terlihat {[k.value if hasattr(k, 'value') else k for k in kinds]}",
        )
        a.rename("baru")
    finally:
        a.close(), b.close()


def scenario_abrupt_disconnect(host: str, port: int) -> None:
    print("\n6. Klien putus mendadak")
    a = connect(host, port, "mendadak")
    watcher = connect(host, port, "penonton")
    try:
        drain(watcher, 0.6)
        # Abort the socket: no DISCONNECT, no FIN, just an RST.
        sock = a.channel._sock  # noqa: SLF001 - the probe needs the raw socket to abort it
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        seen = drain(watcher, 2.0)
        leave = any(m["type"] is MessageType.USER_LEAVE and m["payload"].get("nick") == "mendadak" for m in seen)
        record("server mendeteksi klien hilang", leave, "USER_LEAVE 'mendadak' diterima pengamat")
    finally:
        watcher.close()


def scenario_malformed(host: str, port: int) -> None:
    print("\n7. Frame rusak tidak menjatuhkan server")
    sock, reply = raw_connect(host, port, "perusak")
    try:
        ok = reply.get("type") == MessageType.CONNECT_OK
        record("handshake manual berhasil", ok, f"balasan {reply.get('type')}")
        # A length prefix claiming more bytes than the body actually carries.
        sock.sendall(struct.pack(">I", 4096) + b"\x00" * 8)
        time.sleep(0.4)
        survivor = connect(host, port, "saksi")
        record("server masih melayani setelah frame rusak", True, f"koneksi baru berhasil sebagai {survivor.nickname}")
        survivor.close()
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual scenario harness.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=9009)
    args = parser.parse_args(argv)

    print(f"skenario uji terhadap {args.host}:{args.port}")
    scenario_broadcast(args.host, args.port)
    scenario_private(args.host, args.port)
    scenario_duplicate_nick(args.host, args.port)
    scenario_large_message(args.host, args.port)
    scenario_rename(args.host, args.port)
    scenario_abrupt_disconnect(args.host, args.port)
    scenario_malformed(args.host, args.port)

    passed = sum(1 for _n, verdict, _d in RESULTS if verdict == "LULUS")
    print(f"\nringkasan: {passed}/{len(RESULTS)} skenario lulus")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
