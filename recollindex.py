"""Compatibility shim to expose recollindex module at top-level for tests.

This adds the repository root to `sys.path` so that the
`recollindex` shim can be found when tests run from any subdirectory.
Then it replaces itself with the actual recoll_wrapper.recollindex module
so that all attributes (including private ones) are available.
"""

import pathlib
import sys

# Add the repository root to the import path
repo_root = pathlib.Path(__file__).parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
# Now import and replace this module with the actual implementation
import recoll_wrapper.recollindex
sys.modules[__name__] = recoll_wrapper.recollindex