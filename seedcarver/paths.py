import sys
from pathlib import Path

# The bank lives beside the exe (or repo root), not in PyInstaller's temp dir.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
BANK = APP_DIR / "seeds.bank"
