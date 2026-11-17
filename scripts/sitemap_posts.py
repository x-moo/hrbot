#!/usr/bin/env python3
"""Compatibility wrapper for pipeline.sitemap_posts."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.sitemap_posts import *

if __name__ == "__main__":
    main()
