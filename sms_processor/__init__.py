import os
import pathlib

# Add the src directory of the sms-processor package to the module search path
# The actual implementation lives in ../sms-processor/src/sms_processor
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'sms-processor', 'src'))
if src_path not in __path__:
    __path__.append(src_path)
