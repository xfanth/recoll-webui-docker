import os
import sys
import tempfile

# Ensure the recoll-webui package is on the import path
WEBUI_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'recoll-webui'))
sys.path.insert(0, WEBUI_PATH)

import webui

def test_get_dirs_returns_absolute_paths():
    """Test that get_dirs returns absolute paths and includes '<all>' as the first entry."""
    # Create a temporary directory structure
    with tempfile.TemporaryDirectory() as topdir:
        sub1 = os.path.join(topdir, 'sub1')
        sub2 = os.path.join(topdir, 'sub2')
        os.makedirs(sub1)
        os.makedirs(sub2)
        # Call get_dirs with depth=1
        dirs = webui.get_dirs([topdir], depth=1)
        # The first entry should be '<all>'
        assert dirs[0] == '<all>'
        # All other entries should be absolute paths and exist
        for d in dirs[1:]:
            assert os.path.isabs(d), f"{d} is not absolute"
            assert os.path.isdir(d), f"{d} is not a directory"
