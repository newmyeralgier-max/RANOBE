#!/usr/bin/env python3
"""Thin wrapper so users can run the universal downloader directly.

Usage:
    python3 execution/download_novel.py URL [options]

This is equivalent to ``python3 -m execution.novel_dl URL`` when executed from
the repository root, but it also works when launched via an absolute path or
from within ``execution/`` itself.
"""

from __future__ import annotations

import os
import sys


def _main() -> int:
    # Make the bundled package importable regardless of where the user runs
    # the script from.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from novel_dl.cli import main
    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
