"""Pytest configuration for recoll-audio-worker tests."""

import sys
from pathlib import Path

# Add the project root (where transcribe.py lives) to sys.path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
