"""Local ONNX embeddings (fastembed, `bge-small-en-v1.5`).

Registered but behind an optional extra: onnxruntime and the model weights are
~150 MB, which is not something a default install should pay for before the
provider question (DESIGN §12 Risk 4) has been settled by measurement.

    pip install -e ".[fastembed]"
"""

from __future__ import annotations

from ..base import EmbedError

NAME = "fastembed"
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
ENV_VARS: tuple[str, ...] = ()

WINDOW_CHARS = 1500
"""512-token window, at a pessimistic chars-per-token ratio."""

_MODELS: dict[str, object] = {}


def credential_present() -> bool:
    return True  # runs locally; there is nothing to authenticate


def embed(chunks: list[str], *, model: str, dim: int) -> list[list[float]]:
    """`dim` is ignored — the model fixes it at 384."""
    return [list(v) for v in _model(model).embed(list(chunks))]


def _model(name: str):
    if name not in _MODELS:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbedError(
                'fastembed is an optional extra: pip install -e ".[fastembed]"'
            ) from exc
        _MODELS[name] = TextEmbedding(model_name=name)
    return _MODELS[name]
