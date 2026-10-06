"""Explicit source roots include any legacy code a future adapter imports."""
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import subprocess

from . import __version__

SUITE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SUITE_ROOT.parents[1]


def source_manifest(root):
    root = Path(root).resolve()
    if not root.exists():
        raise ValueError(f"Source root does not exist: {root}")
    entries = {}
    if root.is_file():
        digest = hashlib.sha256(root.read_bytes()).hexdigest()
        return {"root": str(root), "sha256": digest, "files": {root.name: digest}}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".toml", ".json", ".ini", ".txt"} and not {"__pycache__", ".pytest_cache", "results"}.intersection(path.relative_to(root).parts):
            entries[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    import json
    digest = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    return {"root": str(root), "sha256": digest, "files": entries}


def runtime_provenance(*, additional_source_roots=(), dependencies=()):
    """Callers must declare imported bench code/config roots and dependencies.

This is an explicit manifest, not an automatic import dependency graph.
Git dirty status covers the whole repository, including legacy bench edits.
"""
    packages = {}
    for name in dependencies:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    state = {"suite_version": __version__, "python": platform.python_version(),
             "platform": platform.platform(), "dependencies": packages,
             "git_commit": None, "repo_dirty": None,
             "sources": [source_manifest(root) for root in (SUITE_ROOT, *additional_source_roots)]}
    try:
        state["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        state["repo_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL, timeout=5).strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return state
