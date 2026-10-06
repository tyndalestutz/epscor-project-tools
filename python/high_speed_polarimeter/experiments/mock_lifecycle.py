"""Synthetic software event artifact, deliberately not a detector dataset."""
import json


def acquire(session, recipe, directory):
    with (directory / "mock_events.jsonl").open("w", encoding="utf-8") as stream:
        for index in range(3):
            stream.write(json.dumps({"kind": "synthetic_lifecycle_event", "index": index}) + "\n")
            stream.flush()
    print("Saved synthetic lifecycle events; no physical measurements acquired.")
