import os
import sys
from pathlib import Path


_DLL_HANDLES = []


def _runtime_roots() -> list:
    if getattr(sys, "frozen", False):
        roots = [Path(sys.executable).resolve().parent]
        bundle_root = getattr(sys, "_MEIPASS", "")
        if bundle_root:
            roots.append(Path(bundle_root).resolve())
        return roots
    return [Path(__file__).resolve().parents[2]]


def configure_local_dll_paths() -> list:
    """Add bundled native runtime directories before C extension imports."""
    candidates = []
    for root in _runtime_roots():
        candidates.extend([
            root / "runtime" / "cuda",
            root / "ctranslate2",
        ])
    added = []
    for path in candidates:
        if not path.is_dir():
            continue
        path_text = str(path)
        if hasattr(os, "add_dll_directory"):
            _DLL_HANDLES.append(os.add_dll_directory(path_text))
        current_path = os.environ.get("PATH", "")
        parts = [part for part in current_path.split(os.pathsep) if part]
        if path_text.lower() not in {part.lower() for part in parts}:
            os.environ["PATH"] = path_text + os.pathsep + current_path
        added.append(path)
    return added
