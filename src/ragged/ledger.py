"""The idempotency ledger — `.ragged/manifest.json` (DESIGN §5).

Not `okf/manifest.py`, which generates `okf-base.yaml` for okflint. This one is
sidecar state, and it answers the three questions a rebuild has to ask:

  - has this source changed since last time?          -> content_sha256
  - which concepts did it produce?                    -> sources[uri].concepts
  - has a human touched any of them since we wrote?   -> concepts[id].body_sha256

Derived and disposable: deleting it costs a full re-ingest, never correctness.
So every read path degrades to "no record" rather than raising.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .okf import parse

LEDGER_NAME = "manifest.json"
VERSION = 1


def body_hash(body: str) -> str:
    """Hash of a concept body as `render`/`parse` normalise it — both strip."""
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


@dataclass
class SourceRecord:
    content_sha256: str
    size_bytes: int
    mtime: str
    concepts: list[str] = field(default_factory=list)
    """Parent first, then children, in emission order."""
    last_ingest: str = ""
    complete: bool = True
    """False when a unit of this source failed. The concepts that did land are
    still tracked, but the source is re-ingested rather than skipped."""


@dataclass
class ConceptRecord:
    source: str
    body_sha256: str


@dataclass
class Ledger:
    bundle_root: str = "bundle"
    sources: dict[str, SourceRecord] = field(default_factory=dict)
    concepts: dict[str, ConceptRecord] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Ledger:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if data.get("version") != VERSION:
                return cls()
            return cls(
                bundle_root=data.get("bundle_root") or "bundle",
                sources={
                    uri: SourceRecord(**_known(SourceRecord, raw))
                    for uri, raw in (data.get("sources") or {}).items()
                },
                concepts={
                    cid: ConceptRecord(**_known(ConceptRecord, raw))
                    for cid, raw in (data.get("concepts") or {}).items()
                },
            )
        except (OSError, TypeError, ValueError):
            return cls()

    def save(self, path: Path) -> Path:
        # sorted, so a run that changed nothing rewrites a byte-identical file
        payload = {
            "version": VERSION,
            "bundle_root": self.bundle_root,
            "sources": {uri: asdict(r) for uri, r in sorted(self.sources.items())},
            "concepts": {cid: asdict(r) for cid, r in sorted(self.concepts.items())},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path

    def unchanged(self, uri: str, content_sha256: str, bundle_root: Path) -> SourceRecord | None:
        """The record to reuse instead of re-ingesting, or None.

        The concepts must still be on disk: a deleted bundle beside a surviving
        sidecar has to rebuild, not skip its way to an empty one.
        """
        record = self.sources.get(uri)
        if record is None or record.content_sha256 != content_sha256 or not record.concepts:
            return None
        if not record.complete:
            return None  # a section this source owns is still missing
        if any(not (bundle_root / f"{cid}.md").exists() for cid in record.concepts):
            return None
        return record

    def protection(self, bundle_root: Path, concept_id: str) -> str | None:
        """Why this concept must not be overwritten (§6.4), or None.

        With no record — a wiped sidecar — only `verified` protects, because
        "we cannot tell" must not mean "never rebuild anything again".
        """
        path = bundle_root / f"{concept_id}.md"
        if not path.exists():
            return None
        try:
            concept = parse(path.read_text(encoding="utf-8-sig"), concept_id)
        except (OSError, ValueError):
            return None
        if concept.verified:
            return "carries a `verified` block"
        record = self.concepts.get(concept_id)
        if record is not None and body_hash(concept.body) != record.body_sha256:
            return "edited since ragged last wrote it"
        return None

    def note_concept(self, concept_id: str, source_uri: str, body: str) -> None:
        self.concepts[concept_id] = ConceptRecord(source_uri, body_hash(body))

    def note_source(
        self,
        uri: str,
        *,
        content_sha256: str,
        size_bytes: int,
        mtime: str,
        concepts: list[str],
        at: str,
        complete: bool = True,
    ) -> None:
        self.sources[uri] = SourceRecord(
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            mtime=mtime,
            concepts=list(concepts),
            last_ingest=at,
            complete=complete,
        )

    def forget_concept(self, concept_id: str) -> None:
        self.concepts.pop(concept_id, None)

    def forget_source(self, uri: str) -> list[str]:
        """Drop a source and its concept records. Returns the ids it owned."""
        record = self.sources.pop(uri, None)
        if record is None:
            return []
        for concept_id in record.concepts:
            self.concepts.pop(concept_id, None)
        return list(record.concepts)


def _known(cls, raw: object) -> dict:
    """Ignore keys a future version added; a newer sidecar must not crash us."""
    if not isinstance(raw, dict):
        raise TypeError("record is not a mapping")
    return {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
