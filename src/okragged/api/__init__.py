"""The HTTP API (DESIGN.md §11).

`server` is not imported here and neither is FastAPI: the app is built by
`create_app`, which imports the optional extra itself, so importing this package
costs nothing when the extra is absent.
"""

from .app import create_app

__all__ = ["create_app"]
