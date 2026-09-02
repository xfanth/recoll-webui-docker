"""Audio/Video Transcription Worker - re-export from transcribe.py"""
import sys
from pathlib import Path

# Add the parent directory (where transcribe.py lives) to sys.path
_parent = Path(__file__).parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from transcribe import *  # noqa: F403,F401