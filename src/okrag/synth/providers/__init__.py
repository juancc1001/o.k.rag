from __future__ import annotations

from types import ModuleType

from . import claude, gemini

PROVIDERS: dict[str, ModuleType] = {claude.NAME: claude, gemini.NAME: gemini}


def resolve(name: str) -> ModuleType:
    try:
        return PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown synth provider {name!r}; known: {known}") from None


__all__ = ["PROVIDERS", "resolve", "claude", "gemini"]
