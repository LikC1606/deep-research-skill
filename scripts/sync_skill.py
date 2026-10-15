#!/usr/bin/env python3
"""Sync the standalone skill into the plugin, excluding per-run artifacts."""

import argparse
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "deep-research"
DESTINATION = ROOT / "plugins" / "deep-research" / "skills" / "deep-research"


def resources(root):
    return {
        path.relative_to(root): path
        for path in root.rglob("*")
        if path.is_file() and not any(
            part.startswith(".") or part == "__pycache__" or part.endswith(".pyc")
            for part in path.relative_to(root).parts
        )
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report differences without changing files")
    args = parser.parse_args()
    source, destination = resources(SOURCE), resources(DESTINATION)
    extra = sorted(destination.keys() - source.keys())
    changed = [rel for rel, path in source.items()
               if rel not in destination or path.read_bytes() != destination[rel].read_bytes()]
    if args.check:
        for rel in sorted(changed):
            print(f"different or missing: {rel}")
        for rel in extra:
            print(f"plugin-only resource: {rel}")
        if changed or extra:
            return 1
        print(f"Standalone/plugin match: {len(source)} resource files")
        return 0
    if extra:
        raise SystemExit("Plugin-only resources need review before syncing: " + ", ".join(map(str, extra)))
    for rel in changed:
        target = DESTINATION / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source[rel], target)
    print(f"Synced {len(changed)} resource files; runtime data excluded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
