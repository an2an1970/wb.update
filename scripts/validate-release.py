#!/usr/bin/env python3
"""Validate the release tag, pinned components and changelog; optionally export release notes."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
REPOS = ("wb.srv", "wb.ui", "wb.arduino")

def validate(tag, root=ROOT):
    match = VERSION.fullmatch(tag)
    if not match:
        raise ValueError("Expected release tag vMAJOR.MINOR.PATCH, got: " + tag)
    version = tag[1:]
    config = json.loads((root / "release-components.json").read_text(encoding="utf-8"))
    if config.get("version") != version:
        raise ValueError("Release configuration version does not match tag")
    components = config.get("components", {})
    if set(components) != set(REPOS):
        raise ValueError("Expected exactly wb.srv, wb.ui, wb.arduino component pins")
    for repo, spec in components.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("tag"), str) or not spec["tag"]:
            raise ValueError("Missing release tag for " + repo)
        if not isinstance(spec.get("sha"), str) or not SHA.fullmatch(spec["sha"]):
            raise ValueError("Invalid full SHA for " + repo)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = list(re.finditer(r"(?m)^## (.+)$", changelog))
    if not sections or version not in sections[0].group(1):
        raise ValueError("First changelog release section must name version " + version)
    start = sections[0].start()
    end = sections[1].start() if len(sections) > 1 else len(changelog)
    notes = changelog[start:end].strip() + "\n"
    if not re.search(r"(?m)^- \*\*", notes):
        raise ValueError("Release changelog has no aggregated changes")
    return config, notes

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--notes-file")
    args = p.parse_args()
    _, notes = validate(args.tag)
    if args.notes_file:
        Path(args.notes_file).write_text(notes, encoding="utf-8")
    print("Validated release " + args.tag)

if __name__ == "__main__":
    main()
