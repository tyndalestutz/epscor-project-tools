#!/usr/bin/env python3
"""Compatibility launch path for the shared project PAX daemon."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polarization_locking.hardware.pax1000_daemon import ProjectPAX1000

if __name__ == "__main__":
    ProjectPAX1000.main()
