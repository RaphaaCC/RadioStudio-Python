"""Command-line dispatch shared by source and packaged application builds."""

from __future__ import annotations

import sys


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        from .packaging_selftest import run

        return run()

    from .app import main as run_application

    return run_application()
