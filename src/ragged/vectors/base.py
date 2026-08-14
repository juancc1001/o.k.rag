from __future__ import annotations


class EmbedError(RuntimeError):
    """Anything that stopped one concept from being embedded.

    A per-unit error, like `SynthError`: the concept is recorded against and the
    rest of the bundle still lands.
    """


class MissingEmbedCredential(EmbedError):
    pass
