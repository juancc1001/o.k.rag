from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .base import SourceItem


class LocalFileSource:
    name = "local"
    family = "document"

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
