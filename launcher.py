"""Thin entry point for PyInstaller; `python -m savewatch` is the same thing."""

import sys

from savewatch.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
