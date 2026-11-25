"""Small, bundled retrieval corpus. No embeddings, background service or network polling."""
import json
from pathlib import Path


def retrieve(candidates):
    actions = {x['id'] for x in candidates}
    try:
        docs = json.loads((Path(__file__).parent / 'knowledge/memory.json').read_text())
    except (ValueError, OSError):
        return []
    return [d for d in docs if not d['actions'] or actions.intersection(d['actions'])][:4]
