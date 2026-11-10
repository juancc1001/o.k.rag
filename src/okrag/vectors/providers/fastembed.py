"""Local ONNX embeddings (fastembed, `jina-embeddings-v2-base-es`).

Spanish and English in one vector space, reading 8192 tokens: the corpus this was
first run on is Spanish, and an English-only model embeds it badly.

Registered but behind an optional extra: onnxruntime is ~150 MB and the default
model weights another ~640 MB on first use, which is not something a default
install should pay for before the provider question (DESIGN.md §12 Risk 4) has
been settled by measurement.

    pip install -e ".[fastembed]"
"""

from __future__ import annotations

from ..base import EmbedError

NAME = "fastembed"
DEFAULT_MODEL = "jinaai/jina-embeddings-v2-base-es"
ENV_VARS: tuple[str, ...] = ()

WINDOW_CHARS = 24000
"""The default model's 8192-token window, at the same pessimistic ratio as gemini's.
A `[vectors].model` with a shorter window is truncated by fastembed past it."""

_MODELS: dict[str, object] = {}


def credential_present() -> bool:
    return True  # runs locally; there is nothing to authenticate


def embed(chunks: list[str], *, model: str, dim: int) -> list[list[float]]:
    """`dim` is ignored — the model fixes it (768 for the default)."""
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
