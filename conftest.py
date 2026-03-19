from __future__ import annotations

import sys
from pathlib import Path


# Ensure the repository root is on sys.path so tests can import local packages
# like `agenti_helix` when the project isn't installed as a wheel.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Also add backend/ so `import agenti_helix` resolves in tests without install.
_BACKEND_ROOT = _REPO_ROOT / "backend"
if _BACKEND_ROOT.exists() and str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

