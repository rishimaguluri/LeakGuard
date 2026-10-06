"""Shortcuts that work the same on Windows and Mac: python tasks.py <task>."""

from __future__ import annotations

import subprocess
import sys

PY = sys.executable

TASKS: dict[str, list[list[str]]] = {
    "test": [[PY, "-m", "pytest", "-q"]],
    "lint": [[PY, "-m", "ruff", "check", "."], [PY, "-m", "ruff", "format", "--check", "."]],
    "format": [[PY, "-m", "ruff", "format", "."], [PY, "-m", "ruff", "check", "--fix", "."]],
    "check": [[PY, "-m", "ruff", "check", "."], [PY, "-m", "pytest", "-q"]],
    "demo": [[PY, "-m", "leakguard", "demo"]],
    "import": [[PY, "-m", "leakguard", "import"]],
    "match": [[PY, "-m", "leakguard", "match"]],
    "app": [[PY, "-m", "streamlit", "run", "app/Home.py"]],
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS:
        print("Usage: python tasks.py <task>\nTasks: " + ", ".join(TASKS))
        return 1
    for cmd in TASKS[sys.argv[1]]:
        result = subprocess.run(cmd)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
