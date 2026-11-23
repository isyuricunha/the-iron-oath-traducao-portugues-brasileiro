#!/usr/bin/env python3
"""Read-only Linux ARM64 preflight wrapper invoking doctor checks."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

WORK_DIR = Path(__file__).resolve().parent.parent
if str(WORK_DIR) not in sys.path:
    sys.path.insert(0, str(WORK_DIR))

from doctor import run_doctor
from native import Invalid


def main() -> int:
    try:
        report = run_doctor(WORK_DIR.parent)
    except Invalid as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
