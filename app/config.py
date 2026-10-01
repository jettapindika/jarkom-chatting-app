"""Shared configuration: the one place a default address is written down.

Nothing else in the project hardcodes a host or port. Each component reads its
defaults from the environment through the helpers below, and lets its command
line override them, so the same code runs on a laptop, on a lab machine, and in
the demo without an edit.

Precedence: command-line flag > environment variable > default here.
"""

from __future__ import annotations

import os
from typing import Mapping

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MAX_CLIENTS",
    "DEFAULT_PORT",
    "HEARTBEAT_INTERVAL_ENV",
    "HOST_ENV",
    "MAX_CLIENTS_ENV",
    "PORT_ENV",
    "env_flag",
    "env_float",
    "env_int",
    "env_str",
]

#: Where a client connects when nothing else says otherwise. Loopback, not the
#: wildcard address: connecting a client to ``0.0.0.0`` is a misconfiguration
#: whose failure message ("connection refused") explains nothing.
DEFAULT_HOST = "127.0.0.1"

#: The port this project registers for the chat service.
DEFAULT_PORT = 9009

#: Connection ceiling, so a stray stress test cannot exhaust the machine.
DEFAULT_MAX_CLIENTS = 64

HOST_ENV = "CHAT_HOST"
PORT_ENV = "CHAT_PORT"
MAX_CLIENTS_ENV = "CHAT_MAX_CLIENTS"
HEARTBEAT_INTERVAL_ENV = "CHAT_HEARTBEAT_INTERVAL"


def env_str(name: str, default: str, *, environ: Mapping[str, str] | None = None) -> str:
    """Read a string from the environment, falling back to ``default``."""
    source = os.environ if environ is None else environ
    value = source.get(name, "").strip()
    return value or default


def env_int(name: str, default: int, *, environ: Mapping[str, str] | None = None) -> int:
    """Read an integer from the environment, falling back to ``default``.

    A malformed value falls back instead of raising. Someone who typed
    ``CHAT_PORT=abc`` needs the program to start so they can see which variable
    was wrong, not a traceback that names neither.
    """
    raw = env_str(name, "", environ=environ)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float, *, environ: Mapping[str, str] | None = None) -> float:
    """Read a float from the environment, falling back to ``default``."""
    raw = env_str(name, "", environ=environ)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_flag(name: str, default: bool = False, *, environ: Mapping[str, str] | None = None) -> bool:
    """Read a boolean flag from the environment.

    Truthy spellings are ``1``, ``true``, ``yes`` and ``on``, case-insensitive,
    matching what ``--help`` documents and what people actually type.
    """
    raw = env_str(name, "", environ=environ).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}
