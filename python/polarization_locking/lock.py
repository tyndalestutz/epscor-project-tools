"""Compatibility entry point. Prefer python -m polarization_locking."""
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from polarization_locking.cli import main
else:
    from .cli import main


def __getattr__(name):
    if name == "PolarizationLockApp":
        from polarization_locking.app import PolarizationLockApp
        return PolarizationLockApp
    raise AttributeError(name)


if __name__ == "__main__":
    raise SystemExit(main())
