"""Make the repository and an optional mem-guard checkout importable for contract tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_candidates = [
    os.environ.get("MEM_GUARD_PATH", ""),
    str(ROOT.parent / "mem-guard"),
]
for candidate in _candidates:
    if candidate and (Path(candidate) / "mem_guard").is_dir():
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
        break
