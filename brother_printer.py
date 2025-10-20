"""Compatibility wrapper for the old ``brother_printer`` module name."""

from pybrother.cli import *  # noqa: F401,F403
from pybrother.cli import main  # noqa: F401

__all__ = [name for name in globals() if not name.startswith("_")]
