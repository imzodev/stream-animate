#!/usr/bin/env python3
"""Convenience script to run formatting, linting, and tests in sequence."""

from __future__ import annotations

import subprocess
import sys
from typing import Iterable


def run_step(description: str, args: list[str], ok_codes: Iterable[int] = (0,)) -> None:
    print(f"\n=== {description} ===", flush=True)
    result = subprocess.run(args)
    if result.returncode not in ok_codes:
        print(f"{description} failed with exit code {result.returncode}.")
        sys.exit(result.returncode)


def main() -> None:
    run_step("Formatting (black)", ["black", "."])
    run_step("Linting (ruff)", ["ruff", "check", "."])
    run_step("Testing (pytest)", ["pytest"], ok_codes=(0, 5))
    print("\nAll checks passed! ✨")


if __name__ == "__main__":
    main()
