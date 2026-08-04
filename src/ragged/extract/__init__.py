from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PASSTHROUGH_SUFFIXES = {".md", ".markdown", ".txt"}


class ExtractionError(RuntimeError):
    pass


@dataclass
class Extraction:
    text: str
    extractor: str


def extract(path: Path, content_sha256: str, cache_dir: Path | None = None) -> Extraction:
    """Bytes -> markdown-ish text. Cached by content hash, so re-runs are free."""
    cached = _read_cache(cache_dir, content_sha256)
    if cached is not None:
        return cached

    if path.suffix.lower() in PASSTHROUGH_SUFFIXES:
        # utf-8-sig so a BOM doesn't ride into the synthesis prompt
        result = Extraction(path.read_text(encoding="utf-8-sig", errors="replace"), "passthrough")
    else:
        result = _markitdown(path)

    if not result.text.strip():
        raise ExtractionError(f"extracted no text from {path}")

    _write_cache(cache_dir, content_sha256, result)
    return result


def _markitdown(path: Path) -> Extraction:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError(
            f"{path.suffix} needs markitdown; install with: pip install 'markitdown[all]'"
        ) from exc
    try:
        converted = MarkItDown().convert(str(path))
    except Exception as exc:
        raise ExtractionError(f"markitdown failed on {path}: {exc}") from exc
    return Extraction(converted.text_content, "markitdown")


def _cache_path(cache_dir: Path | None, content_sha256: str) -> Path | None:
    return None if cache_dir is None else cache_dir / f"{content_sha256}.json"


def _read_cache(cache_dir: Path | None, content_sha256: str) -> Extraction | None:
    path = _cache_path(cache_dir, content_sha256)
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return Extraction(payload["text"], payload["extractor"])


def _write_cache(cache_dir: Path | None, content_sha256: str, result: Extraction) -> None:
    path = _cache_path(cache_dir, content_sha256)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"text": result.text, "extractor": result.extractor}),
        encoding="utf-8",
        newline="\n",
    )
