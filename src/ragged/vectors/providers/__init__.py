from __future__ import annotations

from types import ModuleType

from . import fastembed, gemini

PROVIDERS: dict[str, ModuleType] = {gemini.NAME: gemini, fastembed.NAME: fastembed}


def resolve(name: str) -> ModuleType:
    try:
        return PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown embedding provider {name!r}; known: {known}") from None


__all__ = ["PROVIDERS", "resolve", "fastembed", "gemini"]
