# Ensures the repo root is importable so `from src import ...` works under pytest.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
