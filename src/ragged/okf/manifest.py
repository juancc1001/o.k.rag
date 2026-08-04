"""Generate `okf-base.yaml`, the manifest okflint needs to run.

Deliberately minimal: `base` + `hygiene`, no `profile`. A profile pins required
fields and controlled vocabularies per type, which is a policy decision for the
bundle owner — and enforcing one would reject the new types synthesis proposes.

Note okflint 0.3.1 only recognises `okf_version: 0.1` and warns on anything
else, so the version is not written here. The bundle-root index.md is where the
bundle declares its OKF version.
"""

from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST_NAME = "okf-base.yaml"


def write_manifest(project_root: Path, bundle_dir: str, name: str) -> Path:
    manifest = {
        "base": {
            "name": name,
            "roots": [{"path": bundle_dir, "exclude_patterns": []}],
            "reserved_files": {"index": "index.md", "log": "log.md"},
        },
        "hygiene": {
            # broken links are explicitly permitted by the spec — warn, never fail
            "broken_links": "warn",
            "reserved_files": "warn",
            "unknown_fields": "off",
            "split_candidates": "warn",
        },
    }
    path = project_root / MANIFEST_NAME
    path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
