#!/usr/bin/env python3
"""Thin launcher: ``python run_server.py --host 0.0.0.0 --port 9009``.

Kept at the project root so the run command is the first thing visible in the
repository, and thin enough that the behaviour lives in :mod:`server.main` where
it can be imported and tested.
"""

from server.main import main

if __name__ == "__main__":
    raise SystemExit(main())
