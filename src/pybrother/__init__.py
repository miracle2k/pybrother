"""Public interface for the pybrother package."""

from importlib import metadata as _metadata

from .cli import main

__all__ = ["main", "__version__"]

try:
    __version__ = _metadata.version("pybrother")
except _metadata.PackageNotFoundError:  # pragma: no cover - during local dev
    __version__ = "0.0.0"

del _metadata
