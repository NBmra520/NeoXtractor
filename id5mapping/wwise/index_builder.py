"""Build the distributable Wwise event hash index.

The shipped file only contains stable, machine independent data:

    FNV-1(event name) -> event name

Event object ids inside `.bnk` banks are exactly `FNV-1(event name)`, so the
index lets the tool turn any event found in a bank into its readable name.
Everything else (media shortIDs, `wwise.thy` keys, md5 names, .idx offsets) is
resolved at runtime from the user's own package, never stored here.

Usage:
    python -m id5mapping.wwise.index_builder --events wwise_audio_length_ex.py \
        --out id5mapping/wwise/wwise_event_hash_index.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time

from core.logger import get_logger
from core.utils import get_application_path

from .hashes import fnv1

INDEX_FORMAT = "neoxtractor-wwise-event-hash-index"
INDEX_VERSION = 1
INDEX_FILE_NAME = "wwise_event_hash_index.json"
_GZ_FILE_NAME = INDEX_FILE_NAME + ".gz"


def bundled_index_path() -> str | None:
    """Path of the index bundled with the Identity V mapping folder."""
    roots = [
        get_application_path(),
        # Also look next to the source tree (e.g. when run via `uv run`).
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ]
    for root in roots:
        data_dir = os.path.join(root, "id5mapping", "wwise")
        for name in (_GZ_FILE_NAME, INDEX_FILE_NAME):
            path = os.path.join(data_dir, name)
            if os.path.exists(path):
                return path
    return None


def normalize_event_name(name: str) -> str:
    """Return the bare event name of a possibly prefixed entry.

    Some tables (e.g. `wwise_replace_event`) store names as
    `amb/Event|P_amb_static_racecourse_horse_vo_hit`; banks always hash the
    part after the last `|`.
    """
    return name.rsplit("|", 1)[-1].strip()


def load_event_names(path: str) -> list[str]:
    """Load event names from a decompiled `.py` or a `.json` file."""
    path = os.path.abspath(path)
    if path.lower().endswith((".json", ".json.gz")):
        opener = gzip.open if path.lower().endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        namespace: dict = {"__name__": "wwise_event_names"}
        with open(path, "r", encoding="utf-8") as handle:
            exec(compile(handle.read(), path, "exec"), namespace)  # noqa: S102
        data = namespace.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"No event dict found in {path}")
    return [normalize_event_name(str(name)) for name in data]


def build_hash_index(events_path: str) -> dict:
    """Compute the FNV-1 hash of every event name."""
    names = load_event_names(events_path)
    events: dict[str, str] = {}
    for name in sorted(names):
        events[str(fnv1(name))] = name
    get_logger().info("Wwise hash index: %d events hashed", len(events))
    return {
        "format": INDEX_FORMAT,
        "version": INDEX_VERSION,
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events": events,
        "stats": {"events": len(events)},
    }


def merge_index(index: dict, path: str) -> dict:
    """Merge a freshly built index into an existing file, never dropping names.

    Event names can disappear from the game scripts while their banks (and
    therefore their hashes) are still shipped, so an update only ever adds.
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return index
    opener = gzip.open if path.lower().endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as handle:
            existing = json.load(handle)
    except (OSError, ValueError):
        get_logger().exception("Wwise: failed to read existing index %s", path)
        return index
    events = dict(existing.get("events") or {})
    events.update(index["events"])
    index["events"] = events
    index["stats"] = {"events": len(events)}
    return index


def save_index(index: dict, path: str) -> int:
    """Write the index as plain JSON or gzip'ed JSON (`.gz` suffix)."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    if path.lower().endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(payload)
    get_logger().info("Wwise: hash index written to %s (%.1f KB)", path, os.path.getsize(path) / 1024)
    return os.path.getsize(path)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: rebuild the bundled Wwise event hash index."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True,
                        help="Event list (.py or .json), only the names are used")
    parser.add_argument(
        "--out",
        default=os.path.join("id5mapping", "wwise", INDEX_FILE_NAME),
        help="Output path (.json or .json.gz)",
    )
    args = parser.parse_args(argv)

    index = build_hash_index(args.events)
    index = merge_index(index, args.out)
    size = save_index(index, args.out)
    print(f"events={index['stats']['events']} size={size / 1024:.0f}KB -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
