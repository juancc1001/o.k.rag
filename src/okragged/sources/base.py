from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Literal, Protocol, runtime_checkable

Family = Literal["document", "asset"]


@dataclass(frozen=True)
class SourceItem:
    uri: str
    """Stable identifier, bundle-independent. Local: repo-relative POSIX path."""
    content_sha256: str
    size_bytes: int
    mtime: datetime
    title: str


@runtime_checkable
class Source(Protocol):
    name: str
    family: Family

    def discover(self) -> Iterator[SourceItem]: ...

    def fetch(self, item: SourceItem) -> bytes | dict[str, Any]:
        """document family returns bytes; asset family returns structured metadata."""
        ...
