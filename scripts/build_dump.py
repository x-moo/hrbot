#!/usr/bin/env python3
"""Compatibility wrapper for pipeline.build_dump."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.build_dump import *

if __name__ == "__main__":
    main()
