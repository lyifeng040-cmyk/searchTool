"""
Legacy loader that reuses the original single-file implementation.
This keeps behavior 100% identical while allowing modular imports.
"""

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_legacy():
    base = Path(__file__).resolve().parent.parent
    legacy_path = base / "2250_Gao-Xing-Neng-Ban_realtime_mod.py"
    if not legacy_path.exists():
        raise FileNotFoundError(f"Legacy file not found: {legacy_path}")

    spec = importlib.util.spec_from_file_location("filesearch._legacy", legacy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load legacy module from {legacy_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("filesearch._legacy", module)
    spec.loader.exec_module(module)
    return module


def legacy_module():
    return load_legacy()
