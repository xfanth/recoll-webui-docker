"""Compatibility shim to expose recollindex module at top-level for tests.

This adds the `recoll_wrapper/src` directory to `sys.path` so that the
`recoll_wrapper.recollindex` module can be imported when the tests run from
the repository root.
"""
import pathlib
import sys

# Add the source directory of the recoll_wrapper package to the import path
src_path = pathlib.Path(__file__).parent / "recoll_wrapper" / "src"
if src_path.is_dir():
    sys.path.insert(0, str(src_path))
# Now import the actual implementation
from recoll_wrapper.recollindex import *
