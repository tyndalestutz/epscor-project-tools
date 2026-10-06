"""Strict JSON artifacts, replaced atomically within the run folder."""
import json
from pathlib import Path


def write_json(path, value):
    path = Path(path)
    text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
