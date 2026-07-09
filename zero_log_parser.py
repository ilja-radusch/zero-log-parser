#!/usr/bin/env python3

"""Standalone entry point for zero-log-parser.

The real implementation lives in the ``zero_log_parser`` package under ``src/``.
This file only bootstraps ``sys.path`` so the package is importable when the
script is run directly (from any working directory) without installation, then
delegates to the package CLI.

It is import-inert: importing this module does nothing. Run it as a script:

   $ python zero_log_parser.py <*.bin file> [-o output_file]

To use the parsing API from Python, install the package (``pip install -e .``)
and ``import zero_log_parser``.
"""

import os
import sys

if __name__ == "__main__":
    _SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    if _SRC in sys.path:
        sys.path.remove(_SRC)
    sys.path.insert(0, _SRC)
    from zero_log_parser.cli import main
    sys.exit(main())
