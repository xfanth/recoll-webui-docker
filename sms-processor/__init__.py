import pathlib
import sys

# Add the src directory to the module search path so imports like 'import sms_processor' work
src_dir = pathlib.Path(__file__).parent / "src"
if src_dir.is_dir():
    sys.path.insert(0, str(src_dir))
