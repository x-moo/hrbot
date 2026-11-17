"""Shared repository paths and .env loading (process environment wins)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env(root=ROOT):
    """Load the first .env found in root or the current directory."""
    for candidate in (Path(root) / '.env', Path.cwd() / '.env'):
        if not candidate.exists():
            continue
        with candidate.open(encoding='utf-8') as stream:
            for line in stream:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                if key:
                    os.environ.setdefault(key, value.strip().strip('"').strip("'"))
        return True
    return False
