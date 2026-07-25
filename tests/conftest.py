"""Shared test setup: headless matplotlib, defensive src/ import fallback."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Thumbnail rendering must never open a GUI window during tests.
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib

matplotlib.use("Agg", force=True)

# Normal path is the editable install; fall back to src/ so the suite still
# collects on a fresh checkout.
_SRC = Path(__file__).resolve().parents[1] / "src"
try:
    import aura_scan  # noqa: F401
except ImportError:  # pragma: no cover
    if _SRC.exists() and str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
