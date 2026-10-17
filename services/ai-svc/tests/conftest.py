"""Make the shared test helpers (simwindows, test_pipeline fakes) importable from sub-folders."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
