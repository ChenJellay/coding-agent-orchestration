from __future__ import annotations

import sys
from pathlib import Path


# Ensure the repository root is on sys.path so tests can import local packages
# like `phase2` and `agenti_helix` when the project isn't installed as a wheel.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

