from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .base import SourceItem


class LocalFileSource:
    name = "local"
    family = "document"
    complete_snapshot = True
    """A directory glob is exhaustive by construction - no paging, no partial
    failure - so absence from `discover()` always licenses deprecation."""

    def __init__(
        self,
        root: Path,
        include: list[str],
        exclude: list[str] | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.root = root
        self.include = include
        self.exclude = exclude or []
        self.project_root = project_root or root.parent

    def discover(self) -> Iterator[SourceItem]:
        seen: set[Path] = set()
        for pattern in self.include:
            for path in sorted(self.root.glob(pattern)):
                if not path.is_file() or path in seen:
                    continue
                if self._excluded(path):
                    continue
                seen.add(path)
                yield self._item(path)

    def matches(self, path: Path) -> bool:
        """Whether `discover` would yield this path. The include globs are
        configurable, so a file written into the tree is not necessarily one
        this source can see.

        Globbing the tree to test one path is deliberate, not an oversight:
        `Path.match` does not treat `**` as recursive, so it answers yes to
        patterns that would never have yielded this file, and `full_match` needs
        3.13. Called once per `add_source`, where a corpus walk is noise beside
        the model call that follows.
        """
        if not path.is_file() or self._excluded(path):
            return False
        return any(path in self.root.glob(pattern) for pattern in self.include)

    def fetch(self, item: SourceItem) -> bytes:
        return (self.project_root / item.uri).read_bytes()

    def _excluded(self, path: Path) -> bool:
        return any(path.match(pattern) for pattern in self.exclude)

    def _item(self, path: Path) -> SourceItem:
        data = path.read_bytes()
        stat = path.stat()
        return SourceItem(
            uri=path.relative_to(self.project_root).as_posix(),
            content_sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            title=path.stem,
        )
