"""Direct-file launcher, matching the polarization-locking suite."""
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from high_speed_polarimeter.__main__ import main
else:
    from .__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
