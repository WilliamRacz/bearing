"""Bearing: keep AI-built projects pointed at what you set out to build."""

import os
from pathlib import Path

__version__ = "0.1.0"

# Where Bearing keeps each project's Logbook, North Star and ratings.
BEARING_HOME = Path(os.environ.get("BEARING_HOME") or Path.home() / ".bearing")
DATA_DIR = BEARING_HOME / "projects"
