#!/usr/bin/env python3
"""Thin launcher: ``python run_client.py --host 127.0.0.1 --port 9009 --nick budi``.

Kept at the project root so the run command is the first thing visible in the
repository, and thin enough that the behaviour lives in :mod:`client.main` where
it can be imported and tested.
"""

from client.main import main

if __name__ == "__main__":
    raise SystemExit(main())
