from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent
src = root / "src"
if src.exists() and str(src) not in sys.path:
    sys.path.insert(0, str(src))

from geo_analyzer.gui.app import main

if __name__ == "__main__":
    main()
