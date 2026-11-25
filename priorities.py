"""Versioned priorities; older settings are migrated without expanding termination permissions."""
import json
from pathlib import Path


def read_priorities(path):
    try:
        data = json.loads(Path(path).read_text())
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get('version') == 2:
        values = data.get('apps')
        if not isinstance(values, dict):
            return {}
        return {k: v for k, v in values.items() if isinstance(k, str) and type(v) is int and 0 <= v <= 999}
    # Earlier close-first/next/last was 1/2/3; 0 and 4 both prohibited closing.
    legacy = {0: 0, 1: 999, 2: 500, 3: 1, 4: 0}
    return {k: legacy[v] for k, v in data.items() if isinstance(k, str) and type(v) is int and v in legacy}
