"""Shared test setup: import paths for the backend package and the web app."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"

# The app imports its modules by bare name (helpers, shared, pages.*), as Voila
# runs it from app/; the backend is importable from its src/ layout.
for path in (REPO / "app", REPO / "advQMcalc_test" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
