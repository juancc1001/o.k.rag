"""Gemini embeddings (google-genai SDK).

Implemented first because the build already requires this credential, so it adds
no dependency and no second key. It is not the answer to DESIGN.md §12 Risk 4 —
that is decided by measuring ghost-edge quality on a real corpus, and swapping is
deleting `embeddings.npy` and re-embedding.
"""

from __future__ import annotations

import os

from ..base import EmbedError, MissingEmbedCredential

NAME = "gemini"
DEFAULT_MODEL = "gemini-embedding-001"
ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

WINDOW_CHARS = 6000
"""Input cap is 2048 tokens; this is that at a deliberately pessimistic ratio,
because a chunk that overflows costs a whole concept and there is no tokenizer
on this side to count with."""


def api_key() -> str:
    for var in ENV_VARS:
        if value := os.environ.get(var):
            return value
    raise MissingEmbedCredential(
        f"no Gemini credential: set {ENV_VARS[0]} (or {ENV_VARS[1]}) in .env or the environment"
    )


def credential_present() -> bool:
    return any(os.environ.get(v) for v in ENV_VARS)


def embed(chunks: list[str], *, model: str, dim: int) -> list[list[float]]:
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=api_key())
    try:
        response = client.models.embed_content(
            model=model,
            contents=list(chunks),
            config=types.EmbedContentConfig(
                task_type="SEMANTIC_SIMILARITY",
                output_dimensionality=dim,
            ),
        )
    except errors.APIError as exc:
        status = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        raise EmbedError(f"{NAME} HTTP {status or 'no status'}: {' '.join(message.split())}") from exc

    vectors = [list(e.values or []) for e in (response.embeddings or [])]
    if len(vectors) != len(chunks):
        raise EmbedError(f"{NAME} returned {len(vectors)} vector(s) for {len(chunks)} chunk(s)")
    return vectors
